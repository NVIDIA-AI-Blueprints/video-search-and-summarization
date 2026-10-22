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

"""Tests for evaluation tasks."""

from datetime import datetime
from unittest.mock import ANY, patch

from bson import ObjectId

from src.api.models import (
    CustomizationResult,
    DatasetType,
    TaskResult,
    ToolEvalType,
    WorkloadClassification,
)
from src.config import (
    DataSplitConfig,
    LLMJobConfig,
    NMPConfig,
)
from src.tasks.tasks import (
    run_base_eval,
    run_customization_eval,
    run_icl_eval,
)
from tests.unit.tasks.conftest import (
    convert_result_to_task_result,
    get_default_job_config,
    get_default_nmp_config,
)


def assert_task_results_equal(result: TaskResult, expected: TaskResult):
    """Compare two TaskResult objects, handling DataSplitConfig union type differences.

    Due to union type ordering (EmbeddingDataSplitConfig | DataSplitConfig),
    a DataSplitConfig may be deserialized as EmbeddingDataSplitConfig with default values.
    This is expected behavior and should not cause test failures.
    """
    # Compare all fields except data_split_config
    assert result.status == expected.status
    assert result.workload_id == expected.workload_id
    assert result.client_id == expected.client_id
    assert result.flywheel_run_id == expected.flywheel_run_id
    assert result.job_type == expected.job_type
    assert result.nim == expected.nim
    assert result.workload_type == expected.workload_type
    assert result.datasets == expected.datasets
    assert result.evaluations == expected.evaluations
    assert result.customization == expected.customization
    assert result.llm_judge_config == expected.llm_judge_config
    assert result.error == expected.error
    assert result.customized_nim == expected.customized_nim
    assert result.job_config == expected.job_config

    # For data_split_config, compare the base DataSplitConfig fields
    if result.data_split_config is None and expected.data_split_config is None:
        pass  # Both None, OK
    elif result.data_split_config is not None and expected.data_split_config is not None:
        # Compare base fields that exist on both DataSplitConfig and EmbeddingDataSplitConfig
        assert result.data_split_config.eval_size == expected.data_split_config.eval_size
        assert result.data_split_config.val_ratio == expected.data_split_config.val_ratio
        assert (
            result.data_split_config.min_total_records
            == expected.data_split_config.min_total_records
        )
        assert result.data_split_config.random_seed == expected.data_split_config.random_seed
        assert result.data_split_config.limit == expected.data_split_config.limit
        assert (
            result.data_split_config.parse_function_arguments
            == expected.data_split_config.parse_function_arguments
        )
    else:
        # One is None, the other isn't - this should fail
        assert result.data_split_config == expected.data_split_config


def prepare_previous_result(valid_nim_config, make_llm_as_judge_config):
    return TaskResult(
        status="success",
        workload_id="test-workload",
        client_id="test-client",
        flywheel_run_id=str(ObjectId()),
        nim=valid_nim_config,
        workload_type=WorkloadClassification.TOOL_CALLING,
        datasets={
            DatasetType.ICL: "test-icl-dataset",
            DatasetType.BASE: "test-base-dataset",
            DatasetType.TRAIN: "test-train-dataset",
        },
        evaluations={},
        customization=None,
        llm_judge_config=make_llm_as_judge_config,
        job_config=LLMJobConfig(**get_default_job_config()),
        data_split_config=DataSplitConfig(**get_default_job_config()["data_split_config"]),
    )


def prepare_previous_result_generic(valid_nim_config, make_llm_as_judge_config):
    return TaskResult(
        status="success",
        workload_id="test-workload",
        client_id="test-client",
        flywheel_run_id=str(ObjectId()),
        nim=valid_nim_config,
        workload_type=WorkloadClassification.GENERIC,
        datasets={
            DatasetType.BASE: "test-base-dataset"  # Need base dataset for evaluation
        },
        evaluations={},
        customization=None,
        llm_judge_config=make_llm_as_judge_config,
        job_config=LLMJobConfig(**get_default_job_config([valid_nim_config])),
        data_split_config=DataSplitConfig(**get_default_job_config()["data_split_config"]),
    )


class TestBaseEvaluation:
    """Tests for base evaluation functionality."""

    def test_run_base_eval(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running base evaluation."""
        nim_id = ObjectId()
        previous_result = prepare_previous_result_generic(
            valid_nim_config, make_llm_as_judge_config
        )
        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = ObjectId()

        # Configure mock evaluator
        mock_evaluator.run_evaluation.return_value = "job-123"
        mock_evaluator.get_job_uri.return_value = "http://test-uri"
        mock_evaluator.get_evaluation_results.return_value = {
            "tasks": {
                "llm-as-judge": {
                    "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.95}}}}
                }
            }
        }

        run_base_eval(previous_result)

        # Verify evaluator calls
        mock_evaluator.run_evaluation.assert_called_once_with(
            dataset_name="test-base-dataset",
            workload_type=WorkloadClassification.GENERIC,
            target_model=valid_nim_config.target_model_for_evaluation(),
            test_file="eval_data.jsonl",
            tool_eval_type=None,
            limit=100,
        )
        mock_evaluator.wait_for_evaluation.assert_called_once()
        mock_evaluator.get_evaluation_results.assert_called_once_with("job-123")

        # Verify DB-helper interactions
        mock_task_db.find_nim_run.assert_called_once()
        mock_task_db.insert_evaluation.assert_called_once()
        assert mock_task_db.update_evaluation.call_count >= 2  # progress + final

    def test_run_base_eval_failure(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running base evaluation when it fails."""
        nim_id = ObjectId()
        eval_id = ObjectId()

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=str(ObjectId()),
            nim=valid_nim_config,
            workload_type=WorkloadClassification.GENERIC,
            datasets={DatasetType.BASE: "test-base-dataset"},
            llm_judge_config=make_llm_as_judge_config,
            job_config=job_config,
            data_split_config=DataSplitConfig(limit=100),
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = eval_id

        # Configure mock evaluator to fail
        mock_evaluator.run_evaluation.side_effect = Exception("Evaluation failed")

        run_base_eval(previous_result)

        # Verify error handling
        mock_task_db.update_evaluation.assert_called_with(
            ANY,
            {
                "error": "Error running base-eval evaluation: Evaluation failed",
                "finished_at": ANY,
                "progress": 0.0,
            },
        )

    def test_run_base_eval_results_failure(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running base evaluation when results retrieval fails."""
        nim_id = ObjectId()
        eval_id = ObjectId()

        previous_result = TaskResult(
            status="success",
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=str(ObjectId()),
            nim=valid_nim_config,
            workload_type=WorkloadClassification.GENERIC,
            datasets={DatasetType.BASE: "test-base-dataset"},
            evaluations={},
            customization=None,
            nmp_config=NMPConfig(**get_default_nmp_config()),
            job_config=job_config,
            llm_judge_config=make_llm_as_judge_config,
            data_split_config=DataSplitConfig(limit=100),
        )

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = eval_id

        # Configure mock evaluator to fail during results retrieval
        mock_evaluator.run_evaluation.return_value = "job-123"
        mock_evaluator.get_job_uri.return_value = "http://test-uri"
        mock_evaluator.wait_for_evaluation.side_effect = Exception("Timeout waiting for evaluation")

        run_base_eval(previous_result)

        # Verify error handling
        mock_task_db.update_evaluation.assert_called_with(
            ANY,
            {
                "error": "Error running base-eval evaluation: Timeout waiting for evaluation",
                "finished_at": ANY,
                "progress": 0.0,
            },
        )


class TestIclEvaluation:
    """Tests for ICL evaluation functionality."""

    def test_run_icl_eval(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running ICL evaluation."""

        nim_id = ObjectId()

        previous_result = prepare_previous_result(valid_nim_config, make_llm_as_judge_config)

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = ObjectId()

        # Configure mock evaluator for tool calling evaluation
        mock_evaluator.run_evaluation.return_value = "job-123"
        mock_evaluator.get_job_uri.return_value = "http://test-uri"
        mock_evaluator.get_evaluation_results.return_value = {
            "tasks": {
                "custom-tool-calling": {
                    "metrics": {
                        "tool-calling-accuracy": {
                            "scores": {
                                "function_name_accuracy": {"value": 0.90},
                                "function_name_and_args_accuracy": {"value": 0.85},
                            }
                        },
                        "correctness": {"scores": {"rating": {"value": 0.88}}},
                    }
                }
            }
        }

        run_icl_eval(previous_result)

        # NMP 26.03 ``metric_jobs.create`` takes one metric per job, so the
        # handler dispatches BOTH the tool-calling accuracy metric and the
        # LLM-judge correctness metric for a TOOL_CALLING workload.
        assert mock_evaluator.run_evaluation.call_count == 2
        called_eval_types = {
            call.kwargs["tool_eval_type"] for call in mock_evaluator.run_evaluation.call_args_list
        }
        assert called_eval_types == {
            ToolEvalType.TOOL_CALLING_METRIC,
            ToolEvalType.TOOL_CALLING_JUDGE,
        }
        for call in mock_evaluator.run_evaluation.call_args_list:
            assert call.kwargs["dataset_name"] == "test-icl-dataset"
            assert call.kwargs["workload_type"] == WorkloadClassification.TOOL_CALLING
            assert call.kwargs["target_model"] == valid_nim_config.target_model_for_evaluation()
            assert call.kwargs["test_file"] == "eval_data.jsonl"
            assert call.kwargs["limit"] == 100

        assert mock_evaluator.wait_for_evaluation.call_count == 2

        # Verify DB-helper interactions — one evaluation record per tool_eval_type.
        assert mock_task_db.find_nim_run.call_count == 2
        assert mock_task_db.insert_evaluation.call_count == 2
        assert mock_task_db.update_evaluation.call_count >= 2  # progress + final

    def test_run_icl_eval_failure(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running ICL evaluation when it fails."""

        nim_id = ObjectId()
        eval_id = ObjectId()
        previous_result = prepare_previous_result(valid_nim_config, make_llm_as_judge_config)

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = eval_id

        # Configure mock evaluator to fail
        mock_evaluator.run_evaluation.side_effect = Exception("Tool calling evaluation failed")

        run_icl_eval(previous_result)

        # Verify error handling
        mock_task_db.update_evaluation.assert_called_with(
            ANY,
            {
                "error": "Error running icl-eval evaluation: Tool calling evaluation failed",
                "finished_at": ANY,
                "progress": 0.0,
            },
        )

    def test_run_icl_eval_results_failure(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running ICL evaluation when results retrieval fails."""

        nim_id = ObjectId()
        eval_id = ObjectId()
        previous_result = prepare_previous_result(valid_nim_config, make_llm_as_judge_config)

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = eval_id

        # Configure mock evaluator to fail during results retrieval
        mock_evaluator.run_evaluation.return_value = "job-123"
        mock_evaluator.get_job_uri.return_value = "http://test-uri"
        mock_evaluator.wait_for_evaluation.side_effect = Exception(
            "Timeout waiting for tool calling evaluation"
        )

        # Create a NIMEvaluation instance with a fixed ID
        with patch("src.api.models.ObjectId", return_value=eval_id):
            run_icl_eval(previous_result)

            # Verify error handling
            mock_task_db.update_evaluation.assert_called_with(
                ANY,
                {
                    "error": "Error running icl-eval evaluation: Timeout waiting for tool calling evaluation",
                    "finished_at": ANY,
                    "progress": 0.0,
                },
            )


class TestCustomizationEvaluation:
    """Tests for customization evaluation functionality."""

    def test_run_customization_eval(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running customization evaluation."""

        nim_id = ObjectId()
        customization = CustomizationResult(
            job_id="test-job",
            model_name="test-model-custom",  # This is the customized model name
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
            epochs_completed=1,
            steps_completed=100,
        )

        previous_result = prepare_previous_result_generic(
            valid_nim_config, make_llm_as_judge_config
        )
        previous_result.customization = customization

        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = ObjectId()
        mock_task_db.find_customization.return_value = {
            "workload_id": "test-workload",
            "customized_model": "test-model-custom",
        }

        # Configure mock evaluator
        mock_evaluator.run_evaluation.return_value = "job-123"
        mock_evaluator.get_job_uri.return_value = "http://test-uri"
        mock_evaluator.get_evaluation_results.return_value = {
            "tasks": {
                "llm-as-judge": {
                    "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.95}}}}
                }
            }
        }

        run_customization_eval(previous_result)

        # Verify evaluator calls
        mock_evaluator.run_evaluation.assert_called_once_with(
            dataset_name="test-base-dataset",
            workload_type=WorkloadClassification.GENERIC,
            target_model="test-model-custom",  # Should use the customized model
            test_file="eval_data.jsonl",
            tool_eval_type=None,
            limit=100,
        )
        mock_evaluator.wait_for_evaluation.assert_called_once()
        mock_evaluator.get_evaluation_results.assert_called_once_with("job-123")

        # Verify DB-helper interactions
        mock_task_db.find_nim_run.assert_called_once()
        mock_task_db.insert_evaluation.assert_called_once()
        assert mock_task_db.update_evaluation.call_count >= 2  # progress + final

    def test_run_customization_eval_failure(
        self,
        mock_evaluator,
        mock_task_db,
        valid_nim_config,
        mock_settings,
        make_llm_as_judge_config,
        job_config,
    ):
        """Test running customization evaluation when it fails."""

        nim_id = ObjectId()
        eval_id = ObjectId()
        customization = CustomizationResult(
            job_id="test-job",
            model_name="test-model-custom",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
            epochs_completed=1,
            steps_completed=100,
        )

        previous_result = prepare_previous_result_generic(
            valid_nim_config, make_llm_as_judge_config
        )
        previous_result.customization = customization
        # Configure DB-helper
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }
        mock_task_db.insert_evaluation.return_value = eval_id
        mock_task_db.find_customization.return_value = {
            "workload_id": "test-workload",
            "customized_model": "test-model-custom",
        }

        # Configure mock evaluator to fail
        mock_evaluator.run_evaluation.side_effect = Exception("Customization evaluation failed")

        run_customization_eval(previous_result)

        # Verify error handling
        mock_task_db.update_evaluation.assert_called_with(
            ANY,
            {
                "error": "Error running customized-eval evaluation: Customization evaluation failed",
                "finished_at": ANY,
                "progress": 0.0,
            },
        )

    def test_run_customization_eval_customization_disabled(
        self, mock_evaluator, mock_task_db, make_llm_as_judge_config, job_config
    ):
        """Test run_customization_eval when customization is disabled (lines 808-809)."""
        nim_id = ObjectId()

        # Create NIM config with customization disabled
        from src.api.models import NIMConfig

        nim_config_disabled = NIMConfig(
            model_name="external-nim-model",
            context_length=2048,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
            registry_base="nvcr.io/nim",
            customization_enabled=False,  # This triggers lines 808-809
        )

        # Create customization result (even though customization is disabled)
        customization = CustomizationResult(
            job_id="test-job",
            model_name="test-model-custom",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
            epochs_completed=1,
            steps_completed=100,
        )
        previous_result = prepare_previous_result_generic(
            nim_config_disabled, make_llm_as_judge_config
        )
        previous_result.customization = customization

        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": nim_config_disabled.model_name,
        }

        result = run_customization_eval(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should return the same result without attempting evaluation (lines 808-809)
        assert_task_results_equal(result, previous_result)
        assert result.nim.customization_enabled is False

        # Verify no evaluation operations occurred
        mock_evaluator.run_evaluation.assert_not_called()
        mock_task_db.insert_evaluation.assert_not_called()
        mock_task_db.find_customization.assert_not_called()

    def test_run_customization_eval_no_customization_model(
        self, mock_evaluator, mock_task_db, valid_nim_config, make_llm_as_judge_config, job_config
    ):
        """Test run_customization_eval when no customization model available."""
        nim_id = ObjectId()

        # Create customization without model_name
        customization_no_model = CustomizationResult(
            job_id="test-job",
            model_name=None,  # No model name - will trigger error
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
            epochs_completed=1,
            steps_completed=100,
        )

        previous_result = prepare_previous_result_generic(
            valid_nim_config, make_llm_as_judge_config
        )
        previous_result.customization = customization_no_model
        # Configure DB manager
        mock_task_db.find_nim_run.return_value = {
            "_id": nim_id,
            "model_name": valid_nim_config.model_name,
        }

        result = run_customization_eval(previous_result)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Verify error was set on the result
        assert result.error is not None
        assert (
            "Error running customization evaluation" in result.error
            or "Error running customized-eval evaluation" in result.error
        )
        assert "No customized model available for evaluation" in result.error

        # Verify no evaluation operations occurred
        mock_evaluator.run_evaluation.assert_not_called()
        mock_task_db.insert_evaluation.assert_not_called()
        mock_task_db.find_customization.assert_not_called()

    def test_run_customization_eval_skip_due_to_previous_error(
        self, mock_evaluator, mock_task_db, valid_nim_config, make_llm_as_judge_config, job_config
    ):
        """Test run_customization_eval skips execution when previous task has error."""
        customization = CustomizationResult(
            job_id="test-job",
            model_name="test-model-custom",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
            epochs_completed=1,
            steps_completed=100,
        )

        # Create previous result with an error
        previous_result_with_error = TaskResult(
            workload_id="test-workload",
            client_id="test-client",
            flywheel_run_id=str(ObjectId()),
            nim=valid_nim_config,
            workload_type=WorkloadClassification.GENERIC,
            datasets={DatasetType.BASE: "test-base-dataset"},
            evaluations={},
            customization=customization,
            llm_judge_config=make_llm_as_judge_config,
            data_split_config=None,
            job_config=job_config,
            error="Previous task failed with error",  # This will cause the stage to be skipped
        )

        result = run_customization_eval(previous_result_with_error)

        # Convert result to TaskResult if it's a dict
        result = convert_result_to_task_result(result)

        # Should return the same result without attempting evaluation
        assert_task_results_equal(result, previous_result_with_error)
        assert result.error == "Previous task failed with error"

        # Verify no evaluation operations occurred
        mock_evaluator.run_evaluation.assert_not_called()
        mock_task_db.insert_evaluation.assert_not_called()
        mock_task_db.find_nim_run.assert_not_called()
        mock_task_db.find_customization.assert_not_called()
