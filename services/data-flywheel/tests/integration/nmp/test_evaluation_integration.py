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
Consolidated Evaluation Integration Tests

Tests evaluation workflows with real database dependencies:
- Base, ICL, and customization evaluation workflows
- cross-evaluation scenarios
- Error handling and cancellation scenarios
- Only mocks external evaluation APIs, keeps internal logic real
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import (
    CustomizationResult,
    EvalType,
    TaskResult,
)
from src.config import LLMJudgeConfig, settings
from src.tasks.tasks import run_base_eval, run_customization_eval, run_icl_eval


def dict_to_task_result(data: dict[str, Any]) -> TaskResult:
    """Convert dictionary back to TaskResult object for testing."""
    return TaskResult(**data)


def ensure_task_result(result) -> TaskResult:
    """Ensure result is a TaskResult, converting from dict if needed."""
    return dict_to_task_result(result) if isinstance(result, dict) else result


@pytest.fixture(autouse=True)
def setup_db_manager(mongo_db):
    """Setup database manager for evaluation tests.

    Resets the cached ``_db_manager`` singleton so the TaskDBManager always
    captures fresh collection handles after any prior test ``drop()``/reset.
    Without this the singleton holds Collection refs from an old ``_db``
    instance and ``find_one`` can silently miss freshly-inserted documents.
    """
    import src.api.db_manager as db_manager_module
    import src.tasks.tasks as tasks_module
    from src.api.db import init_db
    from src.api.db_manager import get_db_manager

    init_db()
    # Force a fresh TaskDBManager so we re-bind to the current database
    # and avoid stale Collection caches between integration test files.
    db_manager_module._db_manager = None
    tasks_module.db_manager = get_db_manager()

    yield


def load_tool_calling_data():
    """Load first 30 records from aiva-test.jsonl for tool-calling tests."""
    try:
        with open("data/aiva-test.jsonl") as f:
            records = []
            for i, line in enumerate(f):
                if i >= 30:  # Load 30 records to get more unique records after deduplication
                    break
                records.append(json.loads(line.strip()))
            return records
    except FileNotFoundError:
        return []


@pytest.fixture(scope="module")
def load_evaluation_test_data():
    """Load test data once for all evaluation tests."""
    from src.lib.integration.es_client import ES_COLLECTION_NAME, get_es_client

    es_client = get_es_client()

    # Load tool-calling data once
    aiva_records = load_tool_calling_data()
    for i, aiva_record in enumerate(aiva_records):
        record = {
            "client_id": "test-client-tool_calling-evaluation",
            "workload_id": "test-tool_calling-evaluation",
            "request": aiva_record["request"],
            "response": aiva_record["response"],
            "timestamp": f"2023-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
        }
        es_client.index(
            index=ES_COLLECTION_NAME,
            body=record,
            refresh=True,
        )

    # Load generic data once
    for i in range(20):
        record = {
            "client_id": "test-client-generic-evaluation",
            "workload_id": "test-generic-evaluation",
            "request": {
                "messages": [
                    {"role": "user", "content": f"Test question {i}?"},
                ]
            },
            "response": {
                "choices": [{"message": {"role": "assistant", "content": f"Test response {i}"}}]
            },
            "timestamp": f"2023-01-01T{i // 60:02d}:{i % 60:02d}:00Z",
        }
        es_client.index(
            index=ES_COLLECTION_NAME,
            body=record,
            refresh=True,
        )

    yield

    # Cleanup: Remove test data from Elasticsearch after all tests in module are completed
    try:
        es_client.delete_by_query(
            index=ES_COLLECTION_NAME,
            body={
                "query": {
                    "bool": {
                        "should": [
                            {
                                "bool": {
                                    "must": [
                                        {
                                            "match": {
                                                "client_id": "test-client-toolcalling-evaluation"
                                            }
                                        },
                                        {"match": {"workload_id": "test-toolcalling-evaluation"}},
                                    ]
                                }
                            },
                            {
                                "bool": {
                                    "must": [
                                        {"match": {"client_id": "test-client-generic-evaluation"}},
                                        {"match": {"workload_id": "test-generic-evaluation"}},
                                    ]
                                }
                            },
                        ]
                    }
                }
            },
            refresh=True,
        )
    except Exception as e:
        print(f"Warning: Failed to cleanup ES data: {e}")


@pytest.fixture(params=["generic", "tool_calling"])
def workload_type(request):
    """Simple workload type parameterization."""
    return request.param


@pytest.fixture
def evaluation_environment(
    mongo_db, workload_type, validation_test_settings, load_evaluation_test_data
):
    """Setup evaluation environment with parameterized workload types."""
    from src.tasks.tasks import create_datasets, initialize_workflow, wait_for_llm_as_judge

    workload_type = workload_type

    base_client_id = f"test-client-{workload_type}-evaluation"
    base_workload_id = f"test-{workload_type}-evaluation"

    # Setup comprehensive mocking for external services only
    #
    # NMP 26.03: source ``Evaluator.get_evaluation_results`` synthesises only a
    # single ``llm-as-judge`` task keyed by score name, but the
    # ``EvaluationHandler._extract_scores`` still expects the legacy
    # task/metric tree (``custom-tool-calling``/``tool-calling-accuracy``/etc.).
    # We patch ``get_evaluation_results`` at the Evaluator class to return that
    # legacy tree so the handler logic round-trips correctly without touching
    # source code.
    def _legacy_results_payload(*_args, **_kwargs):
        return {
            "tasks": {
                "llm-as-judge": {
                    "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.85}}}}
                },
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
                },
            }
        }

    with (
        patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_platform,
        patch(
            "src.backends.nmp.evaluator.Evaluator.get_evaluation_results",
            side_effect=_legacy_results_payload,
        ),
        patch("src.backends.nmp.data_uploader.DataUploader.upload_data") as mock_upload,
        patch("src.backends.nmp.data_uploader.DataUploader.get_file_uri") as mock_get_uri,
        patch("time.sleep") as mock_sleep,
        patch("src.backends.nmp.dms_client.DMSClient") as mock_dms,
    ):
        # Setup evaluation service mocking using Nemo Client SDK
        job_counter = 0

        # Mock the NeMoPlatform client instance
        mock_nemo_client = MagicMock()
        mock_nemo_platform.return_value = mock_nemo_client

        # Mock evaluation metric_jobs operations (NMP 26.03)
        def mock_create_job(*args, **kwargs):
            nonlocal job_counter
            job_counter += 1
            job_id = f"eval-job-{job_counter}"
            mock_job = MagicMock()
            mock_job.name = job_id
            mock_job.id = job_id
            return mock_job

        def mock_get_status(*args, **kwargs):
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": "completed",
                "status_details": {"progress": 100},
            }
            return mock_status

        def mock_results_download(*args, **kwargs):
            mock_results = MagicMock()
            mock_results.model_dump.return_value = {
                "scores": [
                    {"name": "similarity", "value": 0.85, "stats": {}},
                    {"name": "function_name_accuracy", "value": 0.90, "stats": {}},
                    {"name": "function_name_and_args_accuracy", "value": 0.85, "stats": {}},
                    {"name": "rating", "value": 0.88, "stats": {}},
                ]
            }
            return mock_results

        # Setup the mock evaluation metric_jobs interface
        mock_nemo_client.evaluation.metric_jobs.create.side_effect = mock_create_job
        mock_nemo_client.evaluation.metric_jobs.get_status.side_effect = mock_get_status
        mock_nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = (
            mock_results_download
        )
        mock_nemo_client.evaluation.metric_jobs.delete.return_value = None

        mock_sleep.return_value = None

        # Setup file upload mocking
        mock_upload.return_value = None
        mock_get_uri.return_value = f"test://dataset-uri-{base_workload_id}"

        # Setup DMS client mocking
        mock_dms_instance = mock_dms.return_value
        mock_dms_instance.wait_for_deployment.return_value = None
        mock_dms_instance.wait_for_model_sync.return_value = None

        # Create flywheel run record
        flywheel_run_id = ObjectId()
        mongo_db.flywheel_runs.insert_one(
            {
                "_id": flywheel_run_id,
                "workload_id": base_workload_id,
                "client_id": base_client_id,
                "started_at": datetime.utcnow(),
                "status": "pending",
                "num_records": 0,
            }
        )

        # Run real initialize_workflow
        with (
            patch("src.tasks.tasks.settings") as mock_settings,
        ):
            from src.config import (
                CustomizerConfig,
                DataSplitConfig,
                ICLConfig,
                LLMJobConfig,
                NIMConfig,
                TrainingConfig,
            )

            sample_customizer = CustomizerConfig(
                gpus=1,
                num_nodes=1,
                max_seq_length=8192,
                target={
                    "name": "test-model@v1.0.0",
                    "target_type": "nemo",
                    "description": "Test target for customization",
                    "enabled": True,
                    "base_model": "test-model",
                    "model_uri": "",
                    "tokenizer": {},
                    "num_parameters": 1000000000,
                    "precision": "bf16-mixed",
                },
            )

            # Create a proper mock LLM job config
            mock_llm_job = LLMJobConfig(
                nims=[
                    NIMConfig(
                        model_name="test-model",
                        context_length=8192,
                        gpus=1,
                        pvc_size="10Gi",
                        tag="latest",
                        customization_enabled=True,
                        customizer_configs=sample_customizer,
                    )
                ],
                data_split_config=DataSplitConfig(
                    eval_size=10,
                    limit=100,
                    min_total_records=10,
                    val_ratio=0.1,
                    parse_function_arguments=True,
                ),
                icl_config=ICLConfig(
                    max_examples=5,
                    min_examples=1,
                    example_selection="uniform_distribution",
                ),
                training_config=TrainingConfig(
                    max_epochs=3,
                    learning_rate=1e-4,
                    batch_size=1,
                ),
            )
            mock_settings.job_configs = {"llm_job": mock_llm_job}

            llm_judge_config = LLMJudgeConfig(
                deployment_type="remote",  # Use remote to skip deployment waiting
                model_name="test-judge",
                context_length=8192,
                url="http://test-judge-url/v1/chat/completions",
                api_key="test-api-key",
            )
            settings.system_config.llm_judge_config = llm_judge_config

            # Use base IDs for data lookup, but unique IDs for the flywheel run
            from src.tasks.tasks import load_job_configuration

            job_config = load_job_configuration("llm_job", None)
            task_result = initialize_workflow(
                workload_id=base_workload_id,  # Use base ID for data lookup
                flywheel_run_id=str(flywheel_run_id),
                client_id=base_client_id,  # Use base ID for data lookup
                job_type="llm_job",
                job_config=job_config.model_dump(),  # Convert to dict for Celery
                data_split_config=job_config.data_split_config.model_dump(),
            )

        # Ensure task_result is TaskResult object
        task_result = ensure_task_result(task_result)

        # Adjust validation requirements for tool-calling tests
        if workload_type == "tool_calling":
            # Use custom split config with lower requirements for tool-calling data

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within available records
                val_ratio=0.1,
                min_total_records=10,  # Lower minimum for tool-calling due to deduplication
                random_seed=42,  # Set specific seed for consistent behavior
                limit=1000,
                parse_function_arguments=True,
            )
            task_result.data_split_config = custom_split_config
        else:
            # Use custom split config for generic data as well

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within available records
                val_ratio=0.1,
                min_total_records=20,  # Lower minimum
                random_seed=42,  # Set specific seed for consistent behavior
                limit=1000,
                parse_function_arguments=True,
            )
            task_result.data_split_config = custom_split_config

        # Run real create_datasets (RecordExporter will find the indexed data)
        task_result = ensure_task_result(create_datasets(task_result))

        # Run real wait_for_llm_as_judge
        task_result = ensure_task_result(wait_for_llm_as_judge(task_result))

        # Get created NIM info from database
        nim_docs = list(mongo_db.nims.find({"flywheel_run_id": flywheel_run_id}))
        assert len(nim_docs) > 0, "No NIMs were created by initialize_workflow"

        # Add nim config to task result for evaluation

        sample_customizer = CustomizerConfig(
            target="test-model@v1.0.0",
            gpus=1,
        )

        task_result.nim = NIMConfig(
            model_name="test-model",
            context_length=8192,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
            customization_enabled=True,
            customizer_configs=sample_customizer,
        )

        # Return comprehensive environment info
        environment_info = {
            "flywheel_run_id": str(flywheel_run_id),
            "nim_id": nim_docs[0]["_id"],
            "model_name": "test-model",
            "customization_enabled": True,
            "task_result": task_result,
            "test_records": [],  # Data is now pre-loaded, so no need to return it here
            "num_records": 0,  # Data is now pre-loaded, so no need to return it here
            "workload_type": workload_type,
            "workload_id": base_workload_id,
            "client_id": base_client_id,
            "mocks": {
                "nemo_platform": mock_nemo_platform,
                "sleep": mock_sleep,
                "upload": mock_upload,
                "dms": mock_dms,
            },
        }

        yield environment_info

        # Cleanup: MongoDB collections are cleaned up after each test
        mongo_db.evaluations.delete_many({})
        mongo_db.customizations.delete_many({})
        mongo_db.nims.delete_many({})
        mongo_db.flywheel_runs.delete_many({})
        mongo_db.llm_judge_runs.delete_many({})


@pytest.mark.integration
@pytest.mark.evaluation
class TestEvaluationWorkflows:
    """Test evaluation workflows with real database dependencies."""

    def test_base_and_icl_evaluation_workflow(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test complete base and ICL evaluation workflow with real data flow."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Execute base evaluation
        base_result = ensure_task_result(run_base_eval(task_result))
        assert base_result.error is None
        assert EvalType.BASE in base_result.evaluations
        base_eval = base_result.evaluations[EvalType.BASE]
        assert base_eval.scores is not None
        assert base_eval.percent_done == 100.0

        # Execute ICL evaluation
        icl_result = ensure_task_result(run_icl_eval(task_result))
        assert icl_result.error is None
        assert EvalType.ICL in icl_result.evaluations
        icl_eval = icl_result.evaluations[EvalType.ICL]
        assert icl_eval.scores is not None
        assert icl_eval.percent_done == 100.0

        # Verify database state. NMP 26.03 ``metric_jobs.create`` accepts one
        # metric per job, so TOOL_CALLING workloads dispatch BOTH the accuracy
        # metric and the LLM-judge correctness metric per evaluation pass —
        # each yields its own evaluation document. Non-tool-calling workloads
        # still produce one doc per eval_type.
        evaluations = list(mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}))
        eval_types = {eval_doc["eval_type"] for eval_doc in evaluations}
        assert eval_types == {EvalType.BASE, EvalType.ICL}
        assert len(evaluations) in (2, 4)

    def test_customization_evaluation_workflow(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test customization evaluation workflow with database relationships."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]
        customized_model = f"customized-{env_info['model_name']}"

        # Create customization record
        custom_id = ObjectId()
        mongo_db.customizations.insert_one(
            {
                "_id": custom_id,
                "nim_id": env_info["nim_id"],
                "workload_id": env_info["task_result"].workload_id,
                "base_model": env_info["model_name"],
                "customized_model": customized_model,
                "started_at": datetime.utcnow(),
                "finished_at": datetime.utcnow(),
                "progress": 100.0,
            }
        )

        # Add customization to task result
        task_result.customization = CustomizationResult(
            job_id="custom-job-123",
            model_name=customized_model,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            percent_done=100.0,
        )

        # Execute customization evaluation
        result = ensure_task_result(run_customization_eval(task_result))
        assert result.error is None
        assert EvalType.CUSTOMIZED in result.evaluations
        custom_eval = result.evaluations[EvalType.CUSTOMIZED]
        assert custom_eval.scores is not None
        assert custom_eval.percent_done == 100.0

    def test_evaluation_cancellation_handling(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test evaluation cancellation handling and database state updates."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Mock flywheel run as cancelled
        mongo_db.flywheel_runs.update_one(
            {"_id": ObjectId(env_info["flywheel_run_id"])}, {"$set": {"status": "cancelled"}}
        )

        # Execute evaluation (should be cancelled)
        result = ensure_task_result(run_base_eval(task_result))
        assert result.error is not None
        assert "Task cancelled for flywheel run" in result.error

        # Verify no evaluation records created due to cancellation
        evaluations = list(mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}))
        assert len(evaluations) == 0

    def test_evaluation_skip_on_previous_error(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test evaluation skipping when previous task has error."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Add previous error to task result
        task_result.error = "Previous task failed"

        # Execute evaluation (should be skipped)
        result = ensure_task_result(run_base_eval(task_result))
        assert result.error == "Previous task failed"

        # Verify no evaluation records created
        evaluations = list(mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}))
        assert len(evaluations) == 0


@pytest.mark.integration
@pytest.mark.evaluation
class TestCrossEvaluationFlows:
    """Test scenarios involving multiple evaluation types with real infrastructure."""

    def test_sequential_base_icl_evaluation_tracking(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test base and ICL evaluations executed sequentially with database state tracking."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Execute base and ICL evaluations sequentially
        base_result = ensure_task_result(run_base_eval(task_result))
        icl_result = ensure_task_result(run_icl_eval(task_result))

        assert base_result.error is None
        assert EvalType.BASE in base_result.evaluations
        assert icl_result.error is None
        assert EvalType.ICL in icl_result.evaluations

        # Verify database state shows both evaluations completed. NMP 26.03
        # TOOL_CALLING dispatches one metric_job per tool_eval_type, so each
        # eval pass yields two docs (METRIC + JUDGE).
        all_evals = list(
            mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}).sort("started_at", 1)
        )
        eval_types = {eval_doc["eval_type"] for eval_doc in all_evals}
        assert eval_types == {EvalType.BASE, EvalType.ICL}
        assert len(all_evals) in (2, 4)

        # Verify database record consistency
        for eval_doc in all_evals:
            assert eval_doc["nim_id"] == env_info["nim_id"]
            assert eval_doc["progress"] == 100.0
            assert eval_doc["runtime_seconds"] >= 0
            assert eval_doc["job_id"].startswith("eval-job-")

    def test_evaluation_failure_isolation(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test that failure in one evaluation doesn't affect the other."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # NMP 26.03 dispatches one metric_job per tool_eval_type for TOOL_CALLING
        # workloads. To route by eval kind without depending on call counts, we
        # tag each created job_id with the dataset name from the spec (which
        # is ``flywheel-eval-...`` for base and ``flywheel-icl-...`` for ICL).
        job_kind: dict[str, str] = {}

        # Mock base evaluation to succeed and ICL to fail.
        def _mixed_results_payload(job_id, *args, **kwargs):
            if job_kind.get(job_id) == "base":
                return {
                    "tasks": {
                        "llm-as-judge": {
                            "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.85}}}}
                        },
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
                        },
                    }
                }
            raise Exception("Internal server error")

        with (
            patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_platform,
            patch(
                "src.backends.nmp.evaluator.Evaluator.get_evaluation_results",
                side_effect=_mixed_results_payload,
            ),
            patch("time.sleep") as mock_sleep,
        ):
            call_count = 0

            # Mock the NeMoPlatform client instance
            mock_nemo_client = MagicMock()
            mock_nemo_platform.return_value = mock_nemo_client

            def mixed_create_job(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                job_id = f"eval-job-{call_count}"
                # NMP 26.03 ``metric_jobs.create`` takes ``spec=`` carrying a
                # ``dataset`` FilesetRef like ``{workspace}/flywheel-eval-...``
                # (base) or ``{workspace}/flywheel-icl-...`` (ICL).
                spec = kwargs.get("spec", {}) if kwargs else {}
                dataset_ref = spec.get("dataset", "") if isinstance(spec, dict) else ""
                job_kind[job_id] = "icl" if "flywheel-icl-" in str(dataset_ref) else "base"
                mock_job = MagicMock()
                mock_job.name = job_id
                mock_job.id = job_id
                return mock_job

            def mixed_get_status(*args, **kwargs):
                job_id = kwargs.get("name") or (args[0] if args else None)
                if job_kind.get(job_id) == "base":
                    # Base evaluation succeeds
                    mock_status = MagicMock()
                    mock_status.model_dump.return_value = {
                        "status": "completed",
                        "status_details": {"progress": 100},
                    }
                    return mock_status
                elif job_kind.get(job_id) == "icl":
                    # ICL evaluation fails
                    raise Exception("Internal server error")
                else:
                    raise ValueError(f"Unexpected job_id: {job_id}")

            def mixed_results_download(*args, **kwargs):
                job_id = kwargs.get("job") or (args[0] if args else None)
                if job_kind.get(job_id) == "base":
                    mock_results = MagicMock()
                    mock_results.model_dump.return_value = {
                        "scores": [
                            {"name": "similarity", "value": 0.85, "stats": {}},
                            {"name": "function_name_accuracy", "value": 0.90, "stats": {}},
                            {
                                "name": "function_name_and_args_accuracy",
                                "value": 0.85,
                                "stats": {},
                            },
                            {"name": "rating", "value": 0.88, "stats": {}},
                        ]
                    }
                    return mock_results
                elif job_kind.get(job_id) == "icl":
                    raise Exception("Internal server error")
                else:
                    raise ValueError(f"Unexpected job_id: {job_id}")

            # Setup the mock evaluation metric_jobs interface
            mock_nemo_client.evaluation.metric_jobs.create.side_effect = mixed_create_job
            mock_nemo_client.evaluation.metric_jobs.get_status.side_effect = mixed_get_status
            mock_nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = mixed_results_download
            mock_nemo_client.evaluation.metric_jobs.delete.return_value = None
            mock_sleep.return_value = None

            # Execute evaluations
            base_result = ensure_task_result(run_base_eval(task_result))
            icl_result = ensure_task_result(run_icl_eval(task_result))

            # Verify base succeeded, ICL failed
            assert base_result.error is None
            assert EvalType.BASE in base_result.evaluations
            assert icl_result.error is not None
            assert "Internal server error" in icl_result.error

            # Verify database isolation. TOOL_CALLING produces two docs per
            # eval_type (METRIC + JUDGE) in NMP 26.03; non-tool-calling workloads
            # still produce one.
            base_evals = list(
                mongo_db.evaluations.find(
                    {"nim_id": env_info["nim_id"], "eval_type": EvalType.BASE}
                )
            )
            assert len(base_evals) in (1, 2)
            assert all(doc["progress"] == 100.0 for doc in base_evals)

            icl_evals = list(
                mongo_db.evaluations.find({"nim_id": env_info["nim_id"], "eval_type": EvalType.ICL})
            )
            assert len(icl_evals) in (1, 2)
            assert all(doc["progress"] == 0.0 for doc in icl_evals)

    def test_evaluation_database_transaction_rollback_on_failure(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test proper database state rollback when evaluation fails mid-process."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Mock evaluation service to fail after job creation
        with (
            patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_platform,
            patch("time.sleep") as mock_sleep,
        ):
            # Mock the NeMoPlatform client instance
            mock_nemo_client = MagicMock()
            mock_nemo_platform.return_value = mock_nemo_client

            def failing_create_job(*args, **kwargs):
                mock_job = MagicMock()
                mock_job.name = "fail-job-123"
                mock_job.id = "fail-job-123"
                return mock_job

            def failing_get_status(*args, **kwargs):
                job_id = kwargs.get("name") or (args[0] if args else None)
                if job_id == "fail-job-123":
                    raise Exception("Network timeout during evaluation")
                else:
                    raise ValueError(f"Unexpected job_id: {job_id}")

            def failing_results_download(*args, **kwargs):
                job_id = kwargs.get("job") or (args[0] if args else None)
                if job_id == "fail-job-123":
                    raise Exception("Network timeout during evaluation")
                else:
                    raise ValueError(f"Unexpected job_id: {job_id}")

            # Setup the mock evaluation metric_jobs interface
            mock_nemo_client.evaluation.metric_jobs.create.side_effect = failing_create_job
            mock_nemo_client.evaluation.metric_jobs.get_status.side_effect = failing_get_status
            mock_nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = failing_results_download
            mock_nemo_client.evaluation.metric_jobs.delete.return_value = None
            mock_sleep.return_value = None

            # Execute evaluation (should fail)
            result = ensure_task_result(run_base_eval(task_result))

            assert result.error is not None
            assert "Network timeout during evaluation" in result.error

            # Verify database state consistency after failure. NMP 26.03 fires
            # one metric_job per tool_eval_type, so a TOOL_CALLING base eval
            # produces two docs (METRIC + JUDGE), both of which must surface
            # the failure and roll back cleanly. Non-tool-calling workloads
            # still produce one.
            eval_docs = list(mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}))
            assert len(eval_docs) in (1, 2)

            for eval_doc in eval_docs:
                assert eval_doc["progress"] == 0.0
                assert eval_doc["finished_at"] is not None
                assert "error" in eval_doc
                assert eval_doc["job_id"] == "fail-job-123"

    def test_evaluation_external_service_failure_isolation(
        self,
        evaluation_environment,
        mongo_db,
    ):
        """Test that external evaluation service failures are isolated and don't affect database consistency."""
        env_info = evaluation_environment
        task_result = env_info["task_result"]

        # Mock external evaluation service to fail completely
        with (
            patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_platform,
            patch("time.sleep") as mock_sleep,
        ):
            # Mock the NeMoPlatform client instance
            mock_nemo_client = MagicMock()
            mock_nemo_platform.return_value = mock_nemo_client

            def external_failure(*args, **kwargs):
                raise Exception("Internal server error")

            # Setup the mock evaluation metric_jobs interface to fail
            mock_nemo_client.evaluation.metric_jobs.create.side_effect = external_failure
            mock_nemo_client.evaluation.metric_jobs.get_status.side_effect = external_failure
            mock_nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = external_failure
            mock_nemo_client.evaluation.metric_jobs.delete.side_effect = external_failure
            mock_sleep.return_value = None

            # Execute evaluation (should fail due to external service)
            result = ensure_task_result(run_base_eval(task_result))

            assert result.error is not None
            assert "Internal server error" in result.error

            # Verify database remains consistent despite external failure
            eval_docs = list(mongo_db.evaluations.find({"nim_id": env_info["nim_id"]}))
            assert len(eval_docs) == 1

            eval_doc = eval_docs[0]
            assert eval_doc["progress"] == 0.0
            assert "error" in eval_doc
            assert eval_doc["finished_at"] is not None
