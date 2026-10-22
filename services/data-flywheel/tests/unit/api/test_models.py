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

from datetime import datetime

import pytest
from bson import ObjectId
from pydantic import ValidationError

from src.api.models import (
    CustomizationResult,
    DatasetType,
    EvalType,
    EvaluationResult,
    FlywheelRun,
    JobStatus,
    LLMJudgeRun,
    NIMCustomization,
    NIMEvaluation,
    NIMRun,
    TaskResult,
    ToolEvalType,
    WorkloadClassification,
)
from src.api.schemas import Dataset, DeploymentStatus, FlywheelRunStatus, NIMRunStatus
from src.config import DataSplitConfig, LLMJudgeConfig, NIMConfig


class TestEnums:
    """Test cases for enum classes."""

    def test_eval_type_values(self):
        """Test EvalType enum values and methods."""
        assert EvalType.BASE == "base-eval"
        assert EvalType.ICL == "icl-eval"
        assert EvalType.CUSTOMIZED == "customized-eval"

        # Test values() class method
        expected_values = {"base-eval", "icl-eval", "customized-eval"}
        assert EvalType.values() == expected_values

        # Test string representation
        assert str(EvalType.BASE) == "base-eval"
        assert str(EvalType.ICL) == "icl-eval"
        assert str(EvalType.CUSTOMIZED) == "customized-eval"

    def test_dataset_type_values(self):
        """Test DatasetType enum values."""
        assert DatasetType.BASE == "base-dataset"
        assert DatasetType.ICL == "icl-dataset"
        assert DatasetType.TRAIN == "train-dataset"

    def test_workload_classification_values(self):
        """Test WorkloadClassification enum values."""
        assert WorkloadClassification.GENERIC == "generic"
        assert WorkloadClassification.TOOL_CALLING == "tool_calling"

    def test_tool_eval_type_values(self):
        """Test ToolEvalType enum values."""
        assert ToolEvalType.TOOL_CALLING_METRIC == "tool-calling-metric"
        assert ToolEvalType.TOOL_CALLING_JUDGE == "tool-calling-judge"


class TestJobStatus:
    """Test cases for JobStatus model."""

    def test_job_status_creation_with_defaults(self):
        """Test JobStatus creation with default values."""
        job_status = JobStatus()

        assert job_status.job_id is None
        assert job_status.percent_done is None
        assert job_status.started_at is None
        assert job_status.finished_at is None
        assert job_status.status is None

    def test_job_status_creation_with_values(self):
        """Test JobStatus creation with provided values."""
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        job_status = JobStatus(
            job_id="test-job-123",
            percent_done=75.5,
            started_at=started_time,
            finished_at=finished_time,
            status="running",
        )

        assert job_status.job_id == "test-job-123"
        assert job_status.percent_done == 75.5
        assert job_status.started_at == started_time
        assert job_status.finished_at == finished_time
        assert job_status.status == "running"


class TestEvaluationResult:
    """Test cases for EvaluationResult model."""

    def test_evaluation_result_creation_with_defaults(self):
        """Test EvaluationResult creation with default values."""
        eval_result = EvaluationResult()

        # Inherited from JobStatus
        assert eval_result.job_id is None
        assert eval_result.percent_done is None
        assert eval_result.started_at is None
        assert eval_result.finished_at is None
        assert eval_result.status is None

        # EvaluationResult specific
        assert eval_result.scores == {}

    def test_evaluation_result_creation_with_values(self):
        """Test EvaluationResult creation with provided values."""
        started_time = datetime.utcnow()
        scores = {"accuracy": 0.95, "f1_score": 0.87}

        eval_result = EvaluationResult(
            job_id="eval-job-456",
            percent_done=100.0,
            started_at=started_time,
            status="completed",
            scores=scores,
        )

        assert eval_result.job_id == "eval-job-456"
        assert eval_result.percent_done == 100.0
        assert eval_result.started_at == started_time
        assert eval_result.status == "completed"
        assert eval_result.scores == scores


class TestCustomizationResult:
    """Test cases for CustomizationResult model."""

    def test_customization_result_creation_with_defaults(self):
        """Test CustomizationResult creation with default values."""
        custom_result = CustomizationResult()

        # Inherited from JobStatus
        assert custom_result.job_id is None
        assert custom_result.percent_done is None
        assert custom_result.started_at is None
        assert custom_result.finished_at is None
        assert custom_result.status is None

        # CustomizationResult specific
        assert custom_result.model_name is None
        assert custom_result.evaluation_id is None
        assert custom_result.epochs_completed is None
        assert custom_result.steps_completed is None

    def test_customization_result_creation_with_values(self):
        """Test CustomizationResult creation with provided values."""
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        custom_result = CustomizationResult(
            job_id="custom-job-789",
            percent_done=50.0,
            started_at=started_time,
            finished_at=finished_time,
            status="running",
            model_name="test-model",
            evaluation_id="eval-123",
            epochs_completed=5,
            steps_completed=1000,
        )

        assert custom_result.job_id == "custom-job-789"
        assert custom_result.percent_done == 50.0
        assert custom_result.started_at == started_time
        assert custom_result.finished_at == finished_time
        assert custom_result.status == "running"
        assert custom_result.model_name == "test-model"
        assert custom_result.evaluation_id == "eval-123"
        assert custom_result.epochs_completed == 5
        assert custom_result.steps_completed == 1000


class TestTaskResult:
    """Test cases for TaskResult model."""

    def test_task_result_creation_with_defaults(self):
        """Test TaskResult creation with default values."""
        task_result = TaskResult()

        assert task_result.status is None
        assert task_result.workload_id is None
        assert task_result.client_id is None
        assert task_result.flywheel_run_id is None
        assert task_result.nim is None
        assert task_result.workload_type is None
        assert task_result.datasets == {}
        assert task_result.evaluations == {}
        assert task_result.customization is None
        assert task_result.llm_judge_config is None
        assert task_result.error is None
        assert task_result.data_split_config is None

    def test_task_result_creation_with_values(self):
        """Test TaskResult creation with provided values."""
        nim_config = NIMConfig(model_name="test-model", tag="1.0.0", context_length=8192)
        llm_judge_config = LLMJudgeConfig(deployment_type="remote", model_name="judge-model")
        data_split_config = DataSplitConfig(eval_size=100, val_ratio=0.2)

        task_result = TaskResult(
            status="running",
            workload_id="workload-123",
            client_id="client-456",
            flywheel_run_id="flywheel-789",
            nim=nim_config,
            workload_type=WorkloadClassification.GENERIC,
            datasets={"train": "dataset-1", "eval": "dataset-2"},
            llm_judge_config=llm_judge_config,
            error="test error",
            data_split_config=data_split_config,
        )

        assert task_result.status == "running"
        assert task_result.workload_id == "workload-123"
        assert task_result.client_id == "client-456"
        assert task_result.flywheel_run_id == "flywheel-789"
        assert task_result.nim == nim_config
        assert task_result.workload_type == WorkloadClassification.GENERIC
        assert task_result.datasets == {"train": "dataset-1", "eval": "dataset-2"}
        assert task_result.llm_judge_config == llm_judge_config
        assert task_result.error == "test error"
        assert task_result.data_split_config == data_split_config

    def test_add_evaluation(self):
        """Test add_evaluation helper method."""
        task_result = TaskResult()
        eval_result = EvaluationResult(job_id="eval-123", scores={"accuracy": 0.95})

        task_result.add_evaluation(EvalType.BASE, eval_result)

        assert EvalType.BASE in task_result.evaluations
        assert task_result.evaluations[EvalType.BASE] == eval_result

    def test_get_evaluation_existing(self):
        """Test get_evaluation helper method with existing evaluation."""
        task_result = TaskResult()
        eval_result = EvaluationResult(job_id="eval-123", scores={"accuracy": 0.95})

        task_result.add_evaluation(EvalType.ICL, eval_result)
        retrieved = task_result.get_evaluation(EvalType.ICL)

        assert retrieved == eval_result

    def test_get_evaluation_non_existing(self):
        """Test get_evaluation helper method with non-existing evaluation."""
        task_result = TaskResult()

        retrieved = task_result.get_evaluation(EvalType.CUSTOMIZED)

        assert retrieved is None

    def test_task_result_with_embedding_data_split_config(self):
        """Test TaskResult with EmbeddingDataSplitConfig preserves type through serialization."""
        from datetime import date

        from src.config import EmbeddingDataSplitConfig

        # Create TaskResult with EmbeddingDataSplitConfig
        embedding_config = EmbeddingDataSplitConfig(
            eval_size=100,
            val_ratio=0.2,
            min_total_records=50,
            random_seed=42,
            limit=1000,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 1, 31),
            min_negatives=2,
            max_negatives=10,
        )

        task_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id="test-run",
            job_type="embedding_job",
            data_split_config=embedding_config,
        )

        # Verify the config is EmbeddingDataSplitConfig
        assert isinstance(task_result.data_split_config, EmbeddingDataSplitConfig)
        assert task_result.data_split_config.from_date == date(2024, 1, 1)
        assert task_result.data_split_config.to_date == date(2024, 1, 31)
        assert task_result.data_split_config.min_negatives == 2
        assert task_result.data_split_config.max_negatives == 10

        # Simulate Celery serialization/deserialization (JSON round-trip)
        serialized = task_result.model_dump()
        deserialized = TaskResult(**serialized)

        # Verify the config is STILL EmbeddingDataSplitConfig after deserialization
        assert isinstance(deserialized.data_split_config, EmbeddingDataSplitConfig)
        assert deserialized.data_split_config.from_date == date(2024, 1, 1)
        assert deserialized.data_split_config.to_date == date(2024, 1, 31)
        assert deserialized.data_split_config.min_negatives == 2
        assert deserialized.data_split_config.max_negatives == 10

    def test_task_result_with_base_data_split_config(self):
        """Test TaskResult with DataSplitConfig works correctly.

        Note: After deserialization, a base DataSplitConfig may become an
        EmbeddingDataSplitConfig with None values for embedding fields.
        This is expected behavior due to union type ordering and is safe
        because the code checks for None values.
        """
        from src.config import DataSplitConfig, EmbeddingDataSplitConfig

        # Create TaskResult with base DataSplitConfig
        base_config = DataSplitConfig(
            eval_size=100,
            val_ratio=0.2,
            min_total_records=50,
            random_seed=42,
            limit=1000,
        )

        task_result = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id="test-run",
            job_type="llm_job",
            data_split_config=base_config,
        )

        # Verify the config is DataSplitConfig
        assert isinstance(task_result.data_split_config, DataSplitConfig)

        # Simulate Celery serialization/deserialization (JSON round-trip)
        serialized = task_result.model_dump()
        deserialized = TaskResult(**serialized)

        # After deserialization with EmbeddingDataSplitConfig first in union,
        # it may become EmbeddingDataSplitConfig with None values for embedding fields
        # This is safe because the code checks for None
        assert isinstance(deserialized.data_split_config, DataSplitConfig)

        # If it became EmbeddingDataSplitConfig, verify embedding fields are None
        if isinstance(deserialized.data_split_config, EmbeddingDataSplitConfig):
            assert deserialized.data_split_config.from_date is None
            assert deserialized.data_split_config.to_date is None

    def test_update_customization_new(self):
        """Test update_customization helper method creating new customization."""
        task_result = TaskResult()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        task_result.update_customization(
            job_id="custom-job-123",
            model_name="test-model",
            started_at=started_time,
            finished_at=finished_time,
            percent_done=75.0,
            epochs_completed=10,
            steps_completed=2000,
        )

        assert task_result.customization is not None
        assert task_result.customization.job_id == "custom-job-123"
        assert task_result.customization.model_name == "test-model"
        assert task_result.customization.started_at == started_time
        assert task_result.customization.finished_at == finished_time
        assert task_result.customization.percent_done == 75.0
        assert task_result.customization.epochs_completed == 10
        assert task_result.customization.steps_completed == 2000

    def test_update_customization_existing(self):
        """Test update_customization helper method updating existing customization."""
        task_result = TaskResult()
        started_time = datetime.utcnow()

        # Create initial customization
        task_result.update_customization(
            job_id="custom-job-123",
            model_name="test-model",
            started_at=started_time,
            percent_done=25.0,
            epochs_completed=5,
        )

        # Update existing customization
        finished_time = datetime.utcnow()
        task_result.update_customization(
            job_id="custom-job-123",
            model_name="test-model",
            started_at=started_time,
            finished_at=finished_time,
            percent_done=100.0,
            epochs_completed=20,
            steps_completed=4000,
        )

        assert task_result.customization.job_id == "custom-job-123"
        assert task_result.customization.model_name == "test-model"
        assert task_result.customization.started_at == started_time
        assert task_result.customization.finished_at == finished_time
        assert task_result.customization.percent_done == 100.0
        assert task_result.customization.epochs_completed == 20
        assert task_result.customization.steps_completed == 4000

    def test_get_customization_progress_with_customization(self):
        """Test get_customization_progress helper method with existing customization."""
        task_result = TaskResult()
        started_time = datetime.utcnow()

        task_result.update_customization(
            job_id="custom-job-123",
            model_name="test-model",
            started_at=started_time,
            percent_done=60.0,
            epochs_completed=12,
            steps_completed=2400,
        )

        progress = task_result.get_customization_progress()

        expected_progress = {"percent_done": 60.0, "epochs_completed": 12, "steps_completed": 2400}
        assert progress == expected_progress

    def test_get_customization_progress_without_customization(self):
        """Test get_customization_progress helper method without customization."""
        task_result = TaskResult()

        progress = task_result.get_customization_progress()

        assert progress == {}


class TestNIMEvaluation:
    """Test cases for NIMEvaluation model."""

    def test_nim_evaluation_creation_with_defaults(self):
        """Test NIMEvaluation creation with default values."""
        nim_id = ObjectId()
        started_time = datetime.utcnow()

        nim_eval = NIMEvaluation(
            nim_id=nim_id,
            eval_type=EvalType.BASE,
            dataset="test_dataset",
            scores={"accuracy": 0.95},
            started_at=started_time,
            runtime_seconds=120.5,
            progress=100.0,
        )

        assert nim_eval.id is not None
        assert isinstance(nim_eval.id, ObjectId)
        assert nim_eval.nim_id == nim_id
        assert nim_eval.job_id is None
        assert nim_eval.eval_type == EvalType.BASE
        assert nim_eval.dataset == "test_dataset"
        assert nim_eval.scores == {"accuracy": 0.95}
        assert nim_eval.started_at == started_time
        assert nim_eval.finished_at is None
        assert nim_eval.runtime_seconds == 120.5
        assert nim_eval.progress == 100.0
        assert nim_eval.nmp_uri is None
        assert nim_eval.mlflow_uri is None
        assert nim_eval.error is None
        assert nim_eval.eval_name is None

    def test_nim_evaluation_creation_with_all_values(self):
        """Test NIMEvaluation creation with all values provided."""
        eval_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        nim_eval = NIMEvaluation(
            _id=eval_id,
            nim_id=nim_id,
            job_id="eval-job-123",
            eval_type=EvalType.ICL,
            dataset="test_dataset_full",
            eval_name=None,
            scores={"accuracy": 0.87, "f1_score": 0.92},
            started_at=started_time,
            finished_at=finished_time,
            runtime_seconds=300.0,
            progress=100.0,
            nmp_uri="nmp://test/uri",
            error="test error",
        )

        assert nim_eval.id == eval_id
        assert nim_eval.nim_id == nim_id
        assert nim_eval.job_id == "eval-job-123"
        assert nim_eval.eval_type == EvalType.ICL
        assert nim_eval.dataset == "test_dataset_full"
        assert nim_eval.eval_name is None
        assert nim_eval.scores == {"accuracy": 0.87, "f1_score": 0.92}
        assert nim_eval.started_at == started_time
        assert nim_eval.finished_at == finished_time
        assert nim_eval.runtime_seconds == 300.0
        assert nim_eval.progress == 100.0
        assert nim_eval.nmp_uri == "nmp://test/uri"
        assert nim_eval.mlflow_uri is None
        assert nim_eval.error == "test error"

    def test_nim_evaluation_to_mongo(self):
        """Test NIMEvaluation to_mongo method."""
        eval_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        nim_eval = NIMEvaluation(
            _id=eval_id,
            nim_id=nim_id,
            eval_type=EvalType.BASE,
            dataset="test_dataset",
            eval_name=None,
            scores={"accuracy": 0.95},
            started_at=started_time,
            finished_at=finished_time,
            runtime_seconds=120.5,
            progress=100.0,
            nmp_uri="nmp://test/uri",
        )

        mongo_doc = nim_eval.to_mongo()

        expected_doc = {
            "_id": eval_id,
            "nim_id": nim_id,
            "eval_type": EvalType.BASE,
            "dataset": "test_dataset",
            "eval_name": None,
            "scores": {"accuracy": 0.95},
            "started_at": started_time,
            "finished_at": finished_time,
            "runtime_seconds": 120.5,
            "progress": 100.0,
            "nmp_uri": "nmp://test/uri",
            "mlflow_uri": None,
        }
        assert mongo_doc == expected_doc

    def test_nim_evaluation_from_mongo(self):
        """Test NIMEvaluation from_mongo class method."""
        eval_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        mongo_doc = {
            "_id": eval_id,
            "nim_id": nim_id,
            "eval_type": EvalType.ICL,
            "dataset": "test_dataset",
            "eval_name": None,
            "scores": {"accuracy": 0.87},
            "started_at": started_time,
            "finished_at": finished_time,
            "runtime_seconds": 200.0,
            "progress": 100.0,
            "nmp_uri": "nmp://test/uri",
            "mlflow_uri": None,
        }

        nim_eval = NIMEvaluation.from_mongo(mongo_doc)

        assert isinstance(nim_eval, NIMEvaluation)
        assert nim_eval.id == eval_id
        assert isinstance(nim_eval.id, ObjectId)
        assert nim_eval.nim_id == nim_id
        assert nim_eval.eval_type == EvalType.ICL
        assert nim_eval.dataset == "test_dataset"
        assert nim_eval.eval_name is None
        assert nim_eval.scores == {"accuracy": 0.87}
        assert nim_eval.started_at == started_time
        assert nim_eval.finished_at == finished_time
        assert nim_eval.runtime_seconds == 200.0
        assert nim_eval.progress == 100.0
        assert nim_eval.nmp_uri == "nmp://test/uri"
        assert nim_eval.mlflow_uri is None

    def test_nim_evaluation_from_mongo_backward_compatibility(self):
        """Test NIMEvaluation from_mongo handles missing eval_name field for backward compatibility."""
        eval_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        # Old document without eval_name field
        mongo_doc = {
            "_id": eval_id,
            "nim_id": nim_id,
            "eval_type": EvalType.BASE,
            "dataset": "test_dataset",
            "scores": {"accuracy": 0.95},
            "started_at": started_time,
            "finished_at": finished_time,
            "runtime_seconds": 150.0,
            "progress": 100.0,
            "nmp_uri": "nmp://test/uri",
        }

        nim_eval = NIMEvaluation.from_mongo(mongo_doc)

        assert nim_eval.eval_name is None  # Should be None when not present
        assert nim_eval.dataset == "test_dataset"
        assert nim_eval.eval_type == EvalType.BASE


class TestNIMCustomization:
    """Test cases for NIMCustomization model."""

    def test_nim_customization_creation_with_defaults(self):
        """Test NIMCustomization creation with default values."""
        nim_id = ObjectId()
        started_time = datetime.utcnow()

        nim_custom = NIMCustomization(
            nim_id=nim_id,
            workload_id="workload-123",
            base_model="base-model",
            started_at=started_time,
        )

        assert nim_custom.id is not None
        assert isinstance(nim_custom.id, ObjectId)
        assert nim_custom.nim_id == nim_id
        assert nim_custom.job_id is None
        assert nim_custom.workload_id == "workload-123"
        assert nim_custom.base_model == "base-model"
        assert nim_custom.customized_model is None
        assert nim_custom.started_at == started_time
        assert nim_custom.finished_at is None
        assert nim_custom.runtime_seconds == 0.0
        assert nim_custom.progress == 0.0
        assert nim_custom.epochs_completed is None
        assert nim_custom.steps_completed is None
        assert nim_custom.nmp_uri is None
        assert nim_custom.error is None

    def test_nim_customization_creation_with_all_values(self):
        """Test NIMCustomization creation with all values provided."""
        custom_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        nim_custom = NIMCustomization(
            _id=custom_id,
            nim_id=nim_id,
            job_id="custom-job-456",
            workload_id="workload-123",
            base_model="base-model",
            customized_model="custom-model",
            started_at=started_time,
            finished_at=finished_time,
            runtime_seconds=1800.0,
            progress=100.0,
            epochs_completed=20,
            steps_completed=4000,
            nmp_uri="nmp://custom/uri",
            error="test error",
        )

        assert nim_custom.id == custom_id
        assert nim_custom.nim_id == nim_id
        assert nim_custom.job_id == "custom-job-456"
        assert nim_custom.workload_id == "workload-123"
        assert nim_custom.base_model == "base-model"
        assert nim_custom.customized_model == "custom-model"
        assert nim_custom.started_at == started_time
        assert nim_custom.finished_at == finished_time
        assert nim_custom.runtime_seconds == 1800.0
        assert nim_custom.progress == 100.0
        assert nim_custom.epochs_completed == 20
        assert nim_custom.steps_completed == 4000
        assert nim_custom.nmp_uri == "nmp://custom/uri"
        assert nim_custom.error == "test error"

    def test_nim_customization_to_mongo(self):
        """Test NIMCustomization to_mongo method."""
        custom_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()

        nim_custom = NIMCustomization(
            _id=custom_id,
            nim_id=nim_id,
            workload_id="workload-123",
            base_model="base-model",
            started_at=started_time,
            runtime_seconds=600.0,
            progress=50.0,
        )

        mongo_doc = nim_custom.to_mongo()

        # Verify the document contains expected fields
        assert mongo_doc["_id"] == custom_id
        assert mongo_doc["nim_id"] == nim_id
        assert mongo_doc["workload_id"] == "workload-123"
        assert mongo_doc["base_model"] == "base-model"
        assert mongo_doc["started_at"] == started_time
        assert mongo_doc["runtime_seconds"] == 600.0
        assert mongo_doc["progress"] == 50.0

    def test_nim_customization_from_mongo(self):
        """Test NIMCustomization from_mongo class method."""
        custom_id = ObjectId()
        nim_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        mongo_doc = {
            "_id": custom_id,
            "nim_id": nim_id,
            "workload_id": "workload-456",
            "base_model": "base-model-2",
            "customized_model": "custom-model-2",
            "started_at": started_time,
            "finished_at": finished_time,
            "runtime_seconds": 2400.0,
            "progress": 100.0,
            "epochs_completed": 15,
            "steps_completed": 3000,
        }

        nim_custom = NIMCustomization.from_mongo(mongo_doc)

        assert isinstance(nim_custom, NIMCustomization)
        assert nim_custom.id == custom_id
        assert nim_custom.nim_id == nim_id
        assert nim_custom.workload_id == "workload-456"
        assert nim_custom.base_model == "base-model-2"
        assert nim_custom.customized_model == "custom-model-2"
        assert nim_custom.started_at == started_time
        assert nim_custom.finished_at == finished_time
        assert nim_custom.runtime_seconds == 2400.0
        assert nim_custom.progress == 100.0
        assert nim_custom.epochs_completed == 15
        assert nim_custom.steps_completed == 3000

    def test_nim_customization_from_mongo_none(self):
        """Test NIMCustomization from_mongo class method with None input."""
        result = NIMCustomization.from_mongo(None)
        assert result is None

    def test_nim_customization_from_mongo_empty_dict(self):
        """Test NIMCustomization from_mongo class method with empty dict."""
        result = NIMCustomization.from_mongo({})
        assert result is None


class TestNIMRun:
    """Test cases for NIMRun model."""

    def test_nim_run_creation_with_defaults(self):
        """Test NIMRun creation with default values."""
        flywheel_run_id = ObjectId()
        started_time = datetime.utcnow()

        nim_run = NIMRun(
            flywheel_run_id=flywheel_run_id,
            model_name="test-model",
            started_at=started_time,
            runtime_seconds=300.0,
        )

        assert nim_run.id is not None
        assert isinstance(nim_run.id, ObjectId)
        assert nim_run.flywheel_run_id == flywheel_run_id
        assert nim_run.model_name == "test-model"
        assert nim_run.started_at == started_time
        assert nim_run.finished_at is None
        assert nim_run.runtime_seconds == 300.0
        assert nim_run.evaluations == []
        assert nim_run.status is None
        assert nim_run.deployment_status is None
        assert nim_run.error is None

    def test_nim_run_creation_with_all_values(self):
        """Test NIMRun creation with all values provided."""
        run_id = ObjectId()
        flywheel_run_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        # Create evaluation
        eval_id = ObjectId()
        evaluation = NIMEvaluation(
            _id=eval_id,
            nim_id=run_id,
            eval_type=EvalType.BASE,
            dataset="test_dataset",
            scores={"accuracy": 0.95},
            started_at=started_time,
            runtime_seconds=120.0,
            progress=100.0,
        )

        nim_run = NIMRun(
            _id=run_id,
            flywheel_run_id=flywheel_run_id,
            model_name="test-model",
            started_at=started_time,
            finished_at=finished_time,
            runtime_seconds=600.0,
            evaluations=[evaluation],
            status=NIMRunStatus.COMPLETED,
            deployment_status=DeploymentStatus.READY,
            error="test error",
        )

        assert nim_run.id == run_id
        assert nim_run.flywheel_run_id == flywheel_run_id
        assert nim_run.model_name == "test-model"
        assert nim_run.started_at == started_time
        assert nim_run.finished_at == finished_time
        assert nim_run.runtime_seconds == 600.0
        assert len(nim_run.evaluations) == 1
        assert nim_run.evaluations[0] == evaluation
        assert nim_run.status == NIMRunStatus.COMPLETED
        assert nim_run.deployment_status == DeploymentStatus.READY
        assert nim_run.error == "test error"

    def test_nim_run_to_mongo(self):
        """Test NIMRun to_mongo method."""
        run_id = ObjectId()
        flywheel_run_id = ObjectId()
        started_time = datetime.utcnow()

        nim_run = NIMRun(
            _id=run_id,
            flywheel_run_id=flywheel_run_id,
            model_name="test-model",
            started_at=started_time,
            runtime_seconds=300.0,
            status=NIMRunStatus.RUNNING,
        )

        mongo_doc = nim_run.to_mongo()

        # Verify the document contains expected fields
        assert mongo_doc["_id"] == run_id
        assert mongo_doc["flywheel_run_id"] == flywheel_run_id
        assert mongo_doc["model_name"] == "test-model"
        assert mongo_doc["started_at"] == started_time
        assert mongo_doc["runtime_seconds"] == 300.0
        assert mongo_doc["status"] == NIMRunStatus.RUNNING

    def test_nim_run_from_mongo(self):
        """Test NIMRun from_mongo class method."""
        run_id = ObjectId()
        flywheel_run_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        mongo_doc = {
            "_id": run_id,
            "flywheel_run_id": flywheel_run_id,
            "model_name": "test-model-2",
            "started_at": started_time,
            "finished_at": finished_time,
            "runtime_seconds": 900.0,
            "evaluations": [],
            "status": NIMRunStatus.COMPLETED,
            "deployment_status": DeploymentStatus.COMPLETED,
        }

        nim_run = NIMRun.from_mongo(mongo_doc)

        assert isinstance(nim_run, NIMRun)
        assert nim_run.id == run_id
        assert nim_run.flywheel_run_id == flywheel_run_id
        assert nim_run.model_name == "test-model-2"
        assert nim_run.started_at == started_time
        assert nim_run.finished_at == finished_time
        assert nim_run.runtime_seconds == 900.0
        assert nim_run.evaluations == []
        assert nim_run.status == NIMRunStatus.COMPLETED
        assert nim_run.deployment_status == DeploymentStatus.COMPLETED

    def test_nim_run_from_mongo_none(self):
        """Test NIMRun from_mongo class method with None input."""
        result = NIMRun.from_mongo(None)
        assert result is None

    def test_nim_run_from_mongo_empty_dict(self):
        """Test NIMRun from_mongo class method with empty dict."""
        result = NIMRun.from_mongo({})
        assert result is None


class TestLLMJudgeRun:
    """Test cases for LLMJudgeRun model."""

    def test_llm_judge_run_creation_with_defaults(self):
        """Test LLMJudgeRun creation with default values."""
        flywheel_run_id = ObjectId()

        llm_judge = LLMJudgeRun(
            flywheel_run_id=flywheel_run_id, model_name="judge-model", deployment_type="remote"
        )

        assert llm_judge.id is not None
        assert isinstance(llm_judge.id, ObjectId)
        assert llm_judge.flywheel_run_id == flywheel_run_id
        assert llm_judge.model_name == "judge-model"
        assert llm_judge.deployment_type == "remote"
        assert llm_judge.deployment_status is None
        assert llm_judge.error is None

    def test_llm_judge_run_creation_with_all_values(self):
        """Test LLMJudgeRun creation with all values provided."""
        judge_id = ObjectId()
        flywheel_run_id = ObjectId()

        llm_judge = LLMJudgeRun(
            _id=judge_id,
            flywheel_run_id=flywheel_run_id,
            model_name="judge-model",
            deployment_type="local",
            deployment_status=DeploymentStatus.READY,
            error="test error",
        )

        assert llm_judge.id == judge_id
        assert llm_judge.flywheel_run_id == flywheel_run_id
        assert llm_judge.model_name == "judge-model"
        assert llm_judge.deployment_type == "local"
        assert llm_judge.deployment_status == DeploymentStatus.READY
        assert llm_judge.error == "test error"

    def test_llm_judge_run_to_mongo(self):
        """Test LLMJudgeRun to_mongo method."""
        judge_id = ObjectId()
        flywheel_run_id = ObjectId()

        llm_judge = LLMJudgeRun(
            _id=judge_id,
            flywheel_run_id=flywheel_run_id,
            model_name="judge-model",
            deployment_type="remote",
            deployment_status=DeploymentStatus.PENDING,
        )

        mongo_doc = llm_judge.to_mongo()

        # Verify the document contains expected fields
        assert mongo_doc["_id"] == judge_id
        assert mongo_doc["flywheel_run_id"] == flywheel_run_id
        assert mongo_doc["model_name"] == "judge-model"
        assert mongo_doc["deployment_type"] == "remote"
        assert mongo_doc["deployment_status"] == DeploymentStatus.PENDING

    def test_llm_judge_run_from_mongo(self):
        """Test LLMJudgeRun from_mongo class method."""
        judge_id = ObjectId()
        flywheel_run_id = ObjectId()

        mongo_doc = {
            "_id": judge_id,
            "flywheel_run_id": flywheel_run_id,
            "model_name": "judge-model-2",
            "deployment_type": "local",
            "deployment_status": DeploymentStatus.READY,
            "error": "test error",
        }

        llm_judge = LLMJudgeRun.from_mongo(mongo_doc)

        assert isinstance(llm_judge, LLMJudgeRun)
        assert llm_judge.id == judge_id
        assert llm_judge.flywheel_run_id == flywheel_run_id
        assert llm_judge.model_name == "judge-model-2"
        assert llm_judge.deployment_type == "local"
        assert llm_judge.deployment_status == DeploymentStatus.READY
        assert llm_judge.error == "test error"

    def test_llm_judge_run_from_mongo_none(self):
        """Test LLMJudgeRun from_mongo class method with None input."""
        result = LLMJudgeRun.from_mongo(None)
        assert result is None

    def test_llm_judge_run_from_mongo_empty_dict(self):
        """Test LLMJudgeRun from_mongo class method with empty dict."""
        result = LLMJudgeRun.from_mongo({})
        assert result is None


class TestFlywheelRun:
    """Test cases for FlywheelRun model."""

    def test_flywheel_run_creation_with_defaults(self):
        """Test FlywheelRun creation with default values."""
        started_time = datetime.utcnow()

        flywheel_run = FlywheelRun(workload_id="workload-123", started_at=started_time)

        assert flywheel_run.id is not None
        assert isinstance(flywheel_run.id, ObjectId)
        assert flywheel_run.workload_id == "workload-123"
        assert flywheel_run.started_at == started_time
        assert flywheel_run.client_id is None
        assert flywheel_run.status == FlywheelRunStatus.PENDING
        assert flywheel_run.finished_at is None
        assert flywheel_run.num_records is None
        assert flywheel_run.nims == []
        assert flywheel_run.datasets == []
        assert flywheel_run.error is None

    def test_flywheel_run_creation_with_all_values(self):
        """Test FlywheelRun creation with all values provided."""
        run_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        # Create NIM run
        nim_run_id = ObjectId()
        nim_run = NIMRun(
            _id=nim_run_id,
            flywheel_run_id=run_id,
            model_name="test-model",
            started_at=started_time,
            runtime_seconds=300.0,
        )

        # Create dataset
        dataset = Dataset(name="test-dataset", num_records=1000, nmp_uri="nmp://test/dataset")

        flywheel_run = FlywheelRun(
            _id=run_id,
            workload_id="workload-456",
            started_at=started_time,
            client_id="client-789",
            status=FlywheelRunStatus.COMPLETED,
            finished_at=finished_time,
            num_records=1000,
            nims=[nim_run],
            datasets=[dataset],
            error="test error",
        )

        assert flywheel_run.id == run_id
        assert flywheel_run.workload_id == "workload-456"
        assert flywheel_run.started_at == started_time
        assert flywheel_run.client_id == "client-789"
        assert flywheel_run.status == FlywheelRunStatus.COMPLETED
        assert flywheel_run.finished_at == finished_time
        assert flywheel_run.num_records == 1000
        assert len(flywheel_run.nims) == 1
        assert flywheel_run.nims[0] == nim_run
        assert len(flywheel_run.datasets) == 1
        assert flywheel_run.datasets[0] == dataset
        assert flywheel_run.error == "test error"

    def test_flywheel_run_to_mongo(self):
        """Test FlywheelRun to_mongo method."""
        run_id = ObjectId()
        started_time = datetime.utcnow()

        flywheel_run = FlywheelRun(
            _id=run_id,
            workload_id="workload-123",
            started_at=started_time,
            client_id="client-456",
            status=FlywheelRunStatus.RUNNING,
            num_records=500,
        )

        mongo_doc = flywheel_run.to_mongo()

        # Verify the document contains expected fields
        assert mongo_doc["_id"] == run_id
        assert mongo_doc["workload_id"] == "workload-123"
        assert mongo_doc["started_at"] == started_time
        assert mongo_doc["client_id"] == "client-456"
        assert mongo_doc["status"] == FlywheelRunStatus.RUNNING
        assert mongo_doc["num_records"] == 500

    def test_flywheel_run_from_mongo(self):
        """Test FlywheelRun from_mongo class method."""
        run_id = ObjectId()
        started_time = datetime.utcnow()
        finished_time = datetime.utcnow()

        mongo_doc = {
            "_id": run_id,
            "workload_id": "workload-789",
            "started_at": started_time,
            "client_id": "client-123",
            "status": FlywheelRunStatus.FAILED,
            "finished_at": finished_time,
            "num_records": 2000,
            "nims": [],
            "datasets": [],
            "error": "test error",
        }

        flywheel_run = FlywheelRun.from_mongo(mongo_doc)

        assert isinstance(flywheel_run, FlywheelRun)
        assert flywheel_run.id == run_id
        assert flywheel_run.workload_id == "workload-789"
        assert flywheel_run.started_at == started_time
        assert flywheel_run.client_id == "client-123"
        assert flywheel_run.status == FlywheelRunStatus.FAILED
        assert flywheel_run.finished_at == finished_time
        assert flywheel_run.num_records == 2000
        assert flywheel_run.nims == []
        assert flywheel_run.datasets == []
        assert flywheel_run.error == "test error"

    def test_flywheel_run_from_mongo_none(self):
        """Test FlywheelRun from_mongo class method with None input."""
        result = FlywheelRun.from_mongo(None)
        assert result is None

    def test_flywheel_run_from_mongo_empty_dict(self):
        """Test FlywheelRun from_mongo class method with empty dict."""
        result = FlywheelRun.from_mongo({})
        assert result is None


class TestModelValidation:
    """Test cases for model validation and edge cases."""

    def test_task_result_with_invalid_enum_values(self):
        """Test TaskResult with invalid enum values."""
        with pytest.raises(ValidationError):
            TaskResult(workload_type="invalid_workload_type")

    def test_nim_evaluation_with_invalid_eval_type(self):
        """Test NIMEvaluation with invalid eval type."""
        nim_id = ObjectId()
        started_time = datetime.utcnow()

        with pytest.raises(ValidationError):
            NIMEvaluation(
                nim_id=nim_id,
                eval_type="invalid_eval_type",
                scores={"accuracy": 0.95},
                started_at=started_time,
                runtime_seconds=120.0,
                progress=100.0,
            )

    def test_nim_run_with_invalid_status(self):
        """Test NIMRun with invalid status."""
        flywheel_run_id = ObjectId()
        started_time = datetime.utcnow()

        with pytest.raises(ValidationError):
            NIMRun(
                flywheel_run_id=flywheel_run_id,
                model_name="test-model",
                started_at=started_time,
                runtime_seconds=300.0,
                status="invalid_status",
            )

    def test_flywheel_run_with_invalid_status(self):
        """Test FlywheelRun with invalid status."""
        started_time = datetime.utcnow()

        with pytest.raises(ValidationError):
            FlywheelRun(
                workload_id="workload-123", started_at=started_time, status="invalid_status"
            )
