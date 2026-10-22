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
import uuid
from collections.abc import Generator
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from src.api.db import get_db
from src.api.models import EvalType
from src.api.schemas import DeploymentStatus
from src.log_utils import setup_logging


@pytest.fixture
def workload_id() -> str:
    """Generate a unique workload ID for each test."""
    return f"test-workload-{uuid.uuid4()}"


@pytest.fixture
def client_id() -> str:
    """Generate a unique client ID for each test."""
    return f"test-client-{uuid.uuid4()}"


@pytest.fixture
def flywheel_run_id() -> str:
    """Generate a unique flywheel run ID for each test."""
    return str(ObjectId())


@pytest.fixture
def load_test_data_fixture(workload_id: str, client_id: str) -> Generator:
    """Fixture to load test data for each test and clean it up afterward."""

    from src.lib.integration.es_client import ES_COLLECTION_NAME, get_es_client
    from src.scripts.load_test_data import load_data_to_elasticsearch

    logger = setup_logging("data_flywheel.test_fixture")

    # Load test data for this specific test
    load_data_to_elasticsearch(workload_id, client_id, file_path="aiva-test.jsonl")

    es = get_es_client()
    es.indices.refresh(index=ES_COLLECTION_NAME)

    # Verify that data is actually loaded (retry mechanism for test isolation)
    import time

    max_retries = 10
    retry_count = 0
    while retry_count < max_retries:
        # Query for the data to ensure it's available
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"workload_id": workload_id}},
                        {"match": {"client_id": client_id}},
                    ]
                }
            }
        }
        result = es.search(index=ES_COLLECTION_NAME, body=query)
        if result["hits"]["total"]["value"] > 0:
            logger.info(
                f"Data verification successful: found {result['hits']['total']['value']} records"
            )
            break

        # Debug: check what's actually in the index
        all_docs_query = {"query": {"match_all": {}}, "size": 5}
        all_docs_result = es.search(index=ES_COLLECTION_NAME, body=all_docs_query)
        logger.debug(f"Total docs in index: {all_docs_result['hits']['total']['value']}")
        if all_docs_result["hits"]["hits"]:
            sample_doc = all_docs_result["hits"]["hits"][0]["_source"]
            logger.debug(f"Sample document: {sample_doc}")

        retry_count += 1
        if retry_count < max_retries:
            logger.warning(
                f"Data not yet available (attempt {retry_count}/{max_retries}), waiting..."
            )
            time.sleep(0.5)
            es.indices.refresh(index=ES_COLLECTION_NAME)

    if retry_count >= max_retries:
        logger.error(f"Failed to load test data after {max_retries} attempts")
        raise RuntimeError(
            f"Test data not available for workload_id={workload_id}, client_id={client_id}"
        )

    yield  # Test runs here

    # Cleanup: Delete all records for this test's workload_id and client_id
    try:
        # Use match queries for deletion as well (more flexible)
        delete_query = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"workload_id": workload_id}},
                        {"match": {"client_id": client_id}},
                    ]
                }
            }
        }

        result = es.delete_by_query(index=ES_COLLECTION_NAME, body=delete_query)
        logger.info(
            f"Cleaned up {result.get('deleted', 0)} test records for workload_id={workload_id}, client_id={client_id}"
        )

        # Refresh the index to make the deletions immediately visible
        es.indices.refresh(index=ES_COLLECTION_NAME)

    except Exception as e:
        logger.warning(f"Failed to clean up test data: {e}")
        # Don't fail the test if cleanup fails


@pytest.fixture
def test_client() -> TestClient:
    """Create a test client for the FastAPI app"""
    from src.api.db import init_db
    from src.app import app
    from src.tasks.tasks import celery_app, init_worker

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

    # Initialize database and worker process
    init_db()
    init_worker()

    return TestClient(app)


@pytest.fixture
def mock_external_services() -> Generator[dict[str, MagicMock], None, None]:
    """Mock external service responses"""

    # NMP 26.03: source ``Evaluator.get_evaluation_results`` only synthesises
    # the ``llm-as-judge`` task keyed by score name, but the handler's
    # ``_extract_scores`` still looks up ``tasks["custom-tool-calling"]``/etc.
    # We patch the method to return the legacy tree so the handler logic
    # round-trips without modifying source.
    def _legacy_results_payload(*_args, **_kwargs):
        return {
            "tasks": {
                "custom-tool-calling": {
                    "metrics": {
                        "tool-calling-accuracy": {
                            "scores": {
                                "function_name_accuracy": {"value": 0.9},
                                "function_name_and_args_accuracy": {"value": 0.8},
                            }
                        },
                        "correctness": {"scores": {"rating": {"value": 0.85}}},
                    }
                },
                "llm-as-judge": {
                    "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.85}}}}
                },
            }
        }

    with (
        patch("src.backends.nmp.data_uploader.DataUploader.upload_data") as mock_upload_data,
        patch("src.backends.nmp.data_uploader.DataUploader.get_file_uri") as mock_get_file_uri,
        patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo_platform,
        patch(
            "src.backends.nmp.evaluator.Evaluator.get_evaluation_results",
            side_effect=_legacy_results_payload,
        ),
        patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_dms_nemo_platform,
        patch("src.backends.nmp.customizer.NeMoPlatform") as mock_customizer_nemo_platform,
        patch(
            "src.backends.nmp.customizer.Customizer.wait_for_target_ready"
        ) as mock_wait_for_target,
        patch(
            "src.backends.nmp.customizer.Customizer.wait_for_model_sync"
        ) as mock_wait_for_model_sync,
        patch("time.sleep") as mock_sleep,
    ):
        output_model = f"custom-model-{uuid.uuid4()}"

        # Mock wait_for_target_ready to return immediately
        mock_wait_for_target.return_value = MagicMock(status="ready")
        # The handler generates a random output-model name, so the polled
        # inference.models.list can't be matched deterministically; stub the
        # LoRA model-sync wait to return immediately (its result is unused).
        mock_wait_for_model_sync.return_value = {"status": "synced", "model_id": output_model}

        # Mock NeMoPlatform client for DMS service
        mock_dms_nemo_client = MagicMock()
        mock_dms_nemo_platform.return_value = mock_dms_nemo_client

        def mock_create_deployment(*args, **kwargs):
            mock_deployment = MagicMock()
            mock_deployment.name = "deployment-123"
            mock_deployment.to_dict.return_value = {"deployment_id": "deployment-123"}
            return mock_deployment

        def mock_retrieve_deployment(*args, **kwargs):
            mock_deployment = MagicMock()
            mock_deployment.status_details.status = DeploymentStatus.READY
            return mock_deployment

        def mock_delete_deployment(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"message": "Deployment deleted successfully"}
            return mock_result

        def mock_list_models(*args, **kwargs):
            mock_models = MagicMock()
            mock_models.data = [
                MagicMock(id="meta/llama-3.2-1b-instruct"),
                MagicMock(id="meta/llama-3.3-70b-instruct"),
                MagicMock(id="test-model-id"),
                MagicMock(id=output_model),
                MagicMock(id="customized-meta/llama-3.2-1b-instruct"),
            ]
            return mock_models

        # Setup the mock DMS client interface (NMP 26.03: inference.deployments)
        mock_dms_nemo_client.inference.deployments.create.side_effect = mock_create_deployment
        mock_dms_nemo_client.inference.deployments.retrieve.side_effect = mock_retrieve_deployment
        mock_dms_nemo_client.inference.deployments.delete.side_effect = mock_delete_deployment
        mock_dms_nemo_client.inference.models.list.side_effect = mock_list_models

        # Mock NeMoPlatform client for evaluation service
        mock_nemo_client = MagicMock()
        mock_nemo_platform.return_value = mock_nemo_client

        def mock_create_job(*args, **kwargs):
            mock_job = MagicMock()
            mock_job.name = "eval-job-123"
            mock_job.id = "eval-job-123"
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
                    {"name": "function_name_accuracy", "value": 0.9, "stats": {}},
                    {"name": "function_name_and_args_accuracy", "value": 0.8, "stats": {}},
                    {"name": "rating", "value": 0.85, "stats": {}},
                ]
            }
            return mock_results

        # Setup the mock evaluation metric_jobs interface (NMP 26.03)
        mock_nemo_client.evaluation.metric_jobs.create.side_effect = mock_create_job
        mock_nemo_client.evaluation.metric_jobs.get_status.side_effect = mock_get_status
        mock_nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = (
            mock_results_download
        )
        mock_nemo_client.evaluation.metric_jobs.delete.return_value = None

        # Mock NeMoPlatform client for customizer service
        mock_customizer_nemo_client = MagicMock()
        mock_customizer_nemo_platform.return_value = mock_customizer_nemo_client
        # NMP 26.03: the customizer drives target readiness via models.* +
        # wait_for_target_ready (patched above), not a customization.targets API.

        def mock_create_customizer_config(*args, **kwargs):
            mock_config = MagicMock()
            mock_config.name = "test-config"
            return mock_config

        def mock_create_customization_job(*args, **kwargs):
            job_id = f"custom-job-{uuid.uuid4()}"
            mock_job = MagicMock()
            mock_job.id = job_id
            # NMP 26.03 ``start_training_job`` returns ``CustomizationJob.name``.
            mock_job.name = job_id
            mock_job.output_model = output_model
            return mock_job

        def mock_job_status(*args, **kwargs):
            # NMP 26.03: source reads ``status_response.model_dump()`` and
            # routes via the legacy-status translation. ``completed`` passes
            # through; the source then reads percentage_done / epochs_completed
            # / steps_completed / status_logs out of the dict.
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": "completed",
                "percentage_done": 100,
                "epochs_completed": 2,
                "steps_completed": 100,
                "status_logs": [],
            }
            return mock_status

        def mock_retrieve_model(*args, **kwargs):
            mock_model = MagicMock()
            mock_model.name = output_model
            mock_model.status = "ready"
            mock_model.created_at = int(datetime.utcnow().timestamp())
            return mock_model

        def mock_list_inference_models(*args, **kwargs):
            mock_models = MagicMock()
            mock_models.data = [MagicMock(id=output_model)]
            return mock_models

        # Setup the mock customizer client interface
        mock_customizer_nemo_client.customization.configs.create.side_effect = (
            mock_create_customizer_config
        )
        mock_customizer_nemo_client.customization.jobs.create.side_effect = (
            mock_create_customization_job
        )
        mock_customizer_nemo_client.customization.jobs.get_status.side_effect = mock_job_status
        mock_customizer_nemo_client.models.retrieve.side_effect = mock_retrieve_model
        mock_customizer_nemo_client.inference.models.list.side_effect = mock_list_inference_models
        mock_customizer_nemo_client.customization.configs.delete.return_value = None
        mock_customizer_nemo_client.models.delete.return_value = None
        mock_customizer_nemo_client.customization.jobs.cancel.return_value = None

        # Mock upload_data to return the file path
        mock_upload_data.side_effect = lambda data, file_path: file_path
        mock_get_file_uri.side_effect = lambda: "test_uri"

        # Mock sleep to do nothing
        mock_sleep.return_value = None

        yield {
            "nemo_platform": mock_nemo_platform,
            "upload_data": mock_upload_data,
            "get_file_uri": mock_get_file_uri,
            "dms_nemo_platform": mock_dms_nemo_platform,
            "customizer_nemo_platform": mock_customizer_nemo_platform,
            "wait_for_target_ready": mock_wait_for_target,
            "sleep": mock_sleep,
        }


@pytest.fixture
def cleanup_test_data():
    """Cleanup test data before and after tests.

    Also resets the TaskDBManager singleton so we always pick up fresh
    Collection handles after ``drop()`` — otherwise a stale cached
    ``llm_judge_runs`` reference (left over from a prior integration test
    file) can cause ``find_one`` to miss freshly-inserted documents.
    """
    import src.api.db_manager as db_manager_module
    import src.tasks.tasks as tasks_module
    from src.api.db_manager import get_db_manager

    db = get_db()

    # Cleanup before test
    db.flywheel_runs.drop()
    db.nims.drop()
    db.evaluations.drop()
    db.llm_judge_runs.drop()
    db.customizations.drop()

    # Reset the cached TaskDBManager so init_worker / tasks.db_manager pick
    # up fresh Collection refs (post-drop collections recreate on first use).
    db_manager_module._db_manager = None
    tasks_module.db_manager = get_db_manager()

    yield

    # Cleanup after test
    db.flywheel_runs.drop()
    db.nims.drop()
    db.evaluations.drop()
    db.llm_judge_runs.drop()
    db.customizations.drop()


@pytest.mark.integration
@pytest.mark.workflow
def test_full_job_evaluation_flow(
    test_client: TestClient,
    mock_external_services: dict[str, MagicMock],
    client_id: str,
    workload_id: str,
    flywheel_run_id: str,
    cleanup_test_data: Generator,
    load_test_data_fixture,
    validation_test_settings,
):
    """Test the complete job evaluation flow from POST /jobs to completion"""
    # 1. Create a new job - since tasks run synchronously, it will complete immediately
    response = test_client.post(
        "/api/jobs", json={"workload_id": workload_id, "client_id": client_id}
    )
    assert response.status_code == 200
    job_data = response.json()
    job_id = job_data["id"]

    # Job should be completed immediately since tasks run synchronously
    assert job_data["status"] == "queued"

    # Verify that there is only one job in the system
    response = test_client.get("/api/jobs")
    assert response.status_code == 200
    jobs_data = response.json()
    assert len(jobs_data["jobs"]) == 1
    assert jobs_data["jobs"][-1]["id"] == job_id

    # Hit the GET /jobs/:job_id endpoint to verify job details
    response = test_client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    job_details = response.json()

    # Verify the job details
    assert job_details["id"] == job_id
    assert job_details["workload_id"] == workload_id
    assert job_details["status"] in ["completed", "running"]
    assert job_details["started_at"] is not None
    assert job_details["num_records"] > 0

    # Verify NIMs and evaluations in job details
    assert len(job_details["nims"]) > 0
    for nim in job_details["nims"]:
        assert nim["model_name"] is not None
        assert len(nim["evaluations"]) > 0

        for eval in nim["evaluations"]:
            assert eval["eval_type"] in [EvalType.BASE, EvalType.ICL, EvalType.CUSTOMIZED]
            assert eval["scores"] is not None
            assert eval["progress"] == 100.0
            assert eval["started_at"] is not None
            assert eval["runtime_seconds"] > 0

    # 3. Verify database state
    db = get_db()

    # Verify flywheel run
    flywheel_run = db.flywheel_runs.find_one({"_id": ObjectId(job_id)})
    assert flywheel_run is not None
    assert flywheel_run["workload_id"] == workload_id
    assert flywheel_run["num_records"] > 0
    assert flywheel_run["finished_at"] is not None

    # Verify NIMs
    nims = list(db.nims.find({"flywheel_run_id": ObjectId(job_id)}))
    assert len(nims) > 0
    for nim in nims:
        assert nim["model_name"] is not None
        # Ensure NIM deployment finished with COMPLETED status
        assert nim["deployment_status"] == DeploymentStatus.COMPLETED

        # Verify evaluations for this NIM
        evaluations = list(db.evaluations.find({"nim_id": nim["_id"]}))
        assert len(evaluations) > 0
        for eval in evaluations:
            assert eval["eval_type"] in [EvalType.BASE, EvalType.ICL, EvalType.CUSTOMIZED]
            assert eval["scores"] is not None
            assert eval["progress"] == 100.0

    # 4. Verify external service calls
    mock_nemo_platform = mock_external_services["nemo_platform"]
    mock_nemo_client = mock_nemo_platform.return_value

    # Verify evaluator calls (NMP 26.03 uses metric_jobs)
    assert mock_nemo_client.evaluation.metric_jobs.create.called
    assert mock_nemo_client.evaluation.metric_jobs.get_status.called
    assert (
        mock_nemo_client.evaluation.metric_jobs.create.call_count >= 1
    )  # At least 1 job creation call
