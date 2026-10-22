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
Consolidated Customization Integration Tests

Tests customization workflows with real database dependencies:
- Base customization workflow with NIM deployment
- Customization progress tracking and cancellation
- Error handling and rollback scenarios
- Model sync and cleanup workflows
- Only mocks external customization APIs, keeps internal logic real
"""

import json
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

# Use a unique MongoDB database for this test module so concurrent pytest
# processes hitting the shared local mongo (other integration suites do
# `delete_many({})` in fixture teardown) cannot wipe our in-flight inserts.
# Must be set BEFORE the integration conftest's session-scoped ``mongo_db``
# fixture runs ``init_db()`` for the first time. Setting it here at module
# import time happens during pytest collection, which precedes fixture
# instantiation, but only this module's session sees the override because
# pytest collects all integration tests in a single session. To keep this
# safe, the autouse setup_db_manager below also resets ``MONGODB_DB`` and
# forces a fresh db_manager binding for every test.
_TEST_DB_NAME = f"flywheel-cust-{uuid.uuid4().hex[:8]}"
os.environ["MONGODB_DB"] = _TEST_DB_NAME

from src.api.models import (  # noqa: E402 — imports must follow MONGODB_DB override
    TaskResult,
)
from src.config import CustomizerConfig, CustomizerTarget, LLMJudgeConfig, settings  # noqa: E402
from src.tasks.tasks import (  # noqa: E402
    cancel_job_resources,
    finalize_flywheel_run,
    initialize_workflow,
    shutdown_deployment,
    spin_up_nim,
    start_customization,
    wait_for_llm_as_judge,
)


def dict_to_task_result(data: dict[str, Any]) -> TaskResult:
    """Convert dictionary back to TaskResult object for testing."""
    return TaskResult(**data)


def ensure_task_result(result) -> TaskResult:
    """Ensure result is a TaskResult, converting from dict if needed."""
    return dict_to_task_result(result) if isinstance(result, dict) else result


@pytest.fixture(scope="session")
def mongo_db():
    """Override conftest's session-scoped mongo_db to bind to the per-module
    test database (``_TEST_DB_NAME``). This isolates us from other integration
    suites running in parallel that share the local MongoDB and do
    ``delete_many({})`` in teardown.
    """
    import src.api.db as db_module
    from src.api.db import close_db, init_db

    os.environ["MONGODB_DB"] = _TEST_DB_NAME
    if db_module._db is not None and db_module._db.name != _TEST_DB_NAME:
        close_db()
    init_db()
    db = db_module._db
    yield db
    # Drop the per-module test database at session end to keep MongoDB tidy.
    try:
        db.client.drop_database(_TEST_DB_NAME)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def setup_db_manager(mongo_db):
    """Setup database manager for customization tests.

    Forces the TaskDBManager singleton (and underlying ``_db`` global) to
    re-bind to the per-module ``_TEST_DB_NAME`` database on every test.
    Without this, the cached ``_db_manager`` and ``_db`` retain Collection
    objects from another integration suite running in parallel against the
    shared local MongoDB, causing ``find_llm_judge_run`` to either read from
    the wrong db or see its inserts wiped by another suite's
    ``delete_many({})`` teardown.
    """
    import src.api.db as db_module
    import src.api.db_manager as dbm_module
    import src.tasks.tasks as tasks_module
    from src.api.db import close_db, init_db
    from src.api.db_manager import get_db_manager

    # Ensure the env var sticks even if a prior fixture flipped it back.
    os.environ["MONGODB_DB"] = _TEST_DB_NAME

    # Force re-creation of the singleton db connection so it points to the
    # per-module test database (init_db caches the connection otherwise).
    if db_module._db is not None and db_module._db.name != _TEST_DB_NAME:
        close_db()
    init_db()
    dbm_module._db_manager = None
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


@pytest.fixture(params=["generic", "tool_calling"])
def workload_type(request):
    """Simple workload type parameterization."""
    return request.param


@pytest.fixture(scope="module")
def load_customization_test_data():
    """Load test data once for all customization tests."""
    from src.lib.integration.es_client import ES_COLLECTION_NAME, get_es_client

    es_client = get_es_client()

    # Load tool-calling data once
    aiva_records = load_tool_calling_data()
    for i, aiva_record in enumerate(aiva_records):
        record = {
            "client_id": "test-client-tool_calling-customization",
            "workload_id": "test-tool_calling-customization",
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
            "client_id": "test-client-generic-customization",
            "workload_id": "test-generic-customization",
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
                                                "client_id": "test-client-toolcalling-customization"
                                            }
                                        },
                                        {
                                            "match": {
                                                "workload_id": "test-toolcalling-customization"
                                            }
                                        },
                                    ]
                                }
                            },
                            {
                                "bool": {
                                    "must": [
                                        {
                                            "match": {
                                                "client_id": "test-client-generic-customization"
                                            }
                                        },
                                        {"match": {"workload_id": "test-generic-customization"}},
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


@pytest.fixture
def customization_environment(mongo_db, workload_type, load_customization_test_data):
    """Setup customization environment with parameterized workload types.

    Mocks reflect the NMP 26.03 SDK surface used by ``src.backends.nmp.customizer``
    and ``src.backends.nmp.dms_client``:

    - ``customization.jobs.{create, get_status, cancel}``  (was ``status``/legacy ``id``)
    - ``models.{retrieve, create, delete}``                (replaces ``customization.targets/configs``)
    - ``inference.models.list(workspace=...)``             (now workspace-scoped)
    - ``inference.deployments.{create, retrieve, delete}`` with ``status`` attr (not ``status_details``)

    The customized model name is ``f"{workspace}/{output_model_name}"`` in 26.03 where
    ``output_model_name`` is generated randomly per job. We patch ``generate_output_model_name``
    to make the model name deterministic per call so assertions stay stable.
    """
    from src.tasks.tasks import create_datasets

    base_client_id = f"test-client-{workload_type}-customization"
    base_workload_id = f"test-{workload_type}-customization"

    # Setup comprehensive mocking for external services only
    with (
        patch("src.backends.nmp.customizer.NeMoPlatform") as mock_nemo_ms,
        patch(
            "src.backends.nmp.customizer.Customizer.wait_for_target_ready"
        ) as mock_wait_for_target,
        patch(
            "src.tasks.handlers.nmp.customization.generate_output_model_name"
        ) as mock_gen_output_name,
        patch("src.backends.nmp.data_uploader.DataUploader.upload_data") as mock_upload,
        patch("src.backends.nmp.data_uploader.DataUploader.get_file_uri") as mock_get_uri,
        patch("time.sleep") as mock_sleep,
        patch("src.backends.nmp.dms_client.DMSClient") as mock_dms,
        patch("src.backends.nmp.dms_client.NeMoPlatform") as mock_nemo_dms_ms,
        patch("src.backends.nmp.embedding.Embedding") as mock_embedding_class,
        patch("src.backends.nmp.provider.NmpBackendProvider.embedding") as mock_embedding_class_icl,
        patch("src.lib.integration.es_client.index_embeddings_to_es") as mock_index_embeddings,
        patch("src.lib.integration.es_client.search_similar_embeddings") as mock_search_embeddings,
        patch("src.lib.integration.es_client.delete_embeddings_index") as mock_delete_embeddings,
        patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index_embeddings_icl,
        patch(
            "src.lib.flywheel.icl_selection.search_similar_embeddings"
        ) as mock_search_embeddings_icl,
    ):
        # Determine workspace from settings so model paths line up with the source.
        from src.config import settings as _settings

        workspace = _settings.system_config.nmp_config.workspace

        # Setup customization service mocking
        job_counter = 0

        # Deterministic output model names so the customized_model path
        # f"{workspace}/{output_model_name}" is predictable across calls.
        output_name_counter = 0

        def _gen_output_name(*_args, **_kwargs):
            nonlocal output_name_counter
            output_name_counter += 1
            return f"customized-test-model-{output_name_counter}"

        mock_gen_output_name.side_effect = _gen_output_name

        # Mock wait_for_target_ready to return immediately
        mock_wait_for_target.return_value = MagicMock(status="ready")

        # Mock NeMo Platform client for customizer
        mock_customizer_client = mock_nemo_ms.return_value
        mock_customization_jobs = mock_customizer_client.customization.jobs
        mock_models = mock_customizer_client.models
        mock_inference_models = mock_customizer_client.inference.models

        # In 26.03 targets/configs are gone — model entity ops live on client.models.
        # get_target_info(target) calls models.retrieve(name=, workspace=) and reads
        # entity.spec.model_dump() to derive readiness. Return a populated spec so
        # status is "ready" without needing the wait loop.
        def _mock_models_retrieve(**kwargs):
            name = kwargs.get("name", "test-model")
            ws = kwargs.get("workspace", workspace)
            spec = MagicMock()
            spec.model_dump.return_value = {
                "base_model": "nim/test-model",
                "model_uri": "",
                "num_parameters": 1000000000,
                "precision": "bf16-mixed",
                "tokenizer": {},
                "target_type": "nemo",
            }
            return SimpleNamespace(
                id="test-target-id",
                name=name,
                workspace=ws,
                description="Test target",
                spec=spec,
            )

        mock_models.retrieve.side_effect = _mock_models_retrieve

        # models.create / models.delete should not raise. Returning a Mock with a
        # ``.name`` attribute matches Customizer.create_target's logger expectations.
        mock_model_entity = MagicMock()
        mock_model_entity.name = "test-target"
        mock_models.create.return_value = mock_model_entity
        mock_models.delete.return_value = None

        def mock_job_create(**kwargs):
            """Match 26.03 signature: jobs.create(name=, workspace=, spec=)."""
            nonlocal job_counter
            job_counter += 1
            mock_job = MagicMock()
            # In 26.03 the job is keyed by ``name`` (used for status/cancel),
            # not an opaque ``id``. Customizer.start_training_job returns this.
            mock_job.name = f"customization-job-{job_counter}"
            return mock_job

        mock_customization_jobs.create.side_effect = mock_job_create

        # 26.03 SDK exposes get_status (was: status). Returns a Pydantic-like object
        # with model_dump(). Customizer.get_job_status walks ``steps``/``tasks`` for
        # progress and translates "active"→"running", "error"→"failed". For the
        # success path we just return a top-level status="completed" with a
        # progress_pct/epoch/step task so the legacy keys come out populated.
        def mock_job_status(**kwargs):
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": "completed",
                "steps": [
                    {
                        "status": "completed",
                        "status_details": {"message": "training complete"},
                        "tasks": [
                            {
                                "status_details": {
                                    "progress_pct": 100,
                                    "epoch": 3,
                                    "step": 150,
                                }
                            }
                        ],
                    }
                ],
            }
            return mock_status

        mock_customization_jobs.get_status.side_effect = mock_job_status

        # cancel is the 26.03 replacement for the (never-existed-here) legacy cancel.
        mock_customization_jobs.cancel.return_value = None

        # inference.models.list(workspace=...) — wait_for_model_sync inspects ``.data``.
        # Include the customized model paths f"{workspace}/customized-test-model-N"
        # for the deterministic output names produced above.
        def mock_models_list(**_kwargs):
            mock_models_response = MagicMock()
            entries = [
                "nim/test-model",
                f"{workspace}/customized-test-model",
                f"{workspace}/customized-test-model-1",
                f"{workspace}/customized-test-model-2",
                f"{workspace}/customized-test-model-3",
                f"{workspace}/customized-test-model-4",
            ]
            mocks = []
            for entry in entries:
                m = MagicMock()
                m.id = entry
                mocks.append(m)
            mock_models_response.data = mocks
            return mock_models_response

        mock_inference_models.list.side_effect = mock_models_list
        mock_sleep.return_value = None

        # ----- DMS client (deployment side of NeMoPlatform) -----
        mock_dms_client = mock_nemo_dms_ms.return_value
        mock_dms_deployments = mock_dms_client.inference.deployments
        mock_dms_inference_models = mock_dms_client.inference.models
        mock_dms_deployment_configs = mock_dms_client.inference.deployment_configs

        # Deployment config creation is best-effort; just return a Mock.
        mock_dms_deployment_configs.create.return_value = MagicMock(name="dep-config")

        def mock_deployment_create(**kwargs):
            mock_deployment = MagicMock()
            mock_deployment.name = kwargs.get("name", "deployment-123")
            mock_deployment.model_dump.return_value = {"id": "deployment-123", "status": "READY"}
            return mock_deployment

        mock_dms_deployments.create.side_effect = mock_deployment_create

        # 26.03 reads ``deployment.status`` directly (was: status_details.status).
        # Source maps "READY" → "ready" via _to_legacy_status.
        def mock_deployment_retrieve(**kwargs):
            return SimpleNamespace(status="READY", name=kwargs.get("name", "deployment-123"))

        mock_dms_deployments.retrieve.side_effect = mock_deployment_retrieve

        def mock_deployment_delete(**kwargs):
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"status": "deleted"}
            return mock_result

        mock_dms_deployments.delete.side_effect = mock_deployment_delete

        # Source calls models.list(workspace=...) — accept arbitrary kwargs.
        def mock_dms_models_list(**_kwargs):
            mock_models_response = MagicMock()
            entries = ["nim/test-model", f"{workspace}/customized-test-model"]
            mocks = []
            for entry in entries:
                m = MagicMock()
                m.id = entry
                mocks.append(m)
            mock_models_response.data = mocks
            return mock_models_response

        mock_dms_inference_models.list.side_effect = mock_dms_models_list

        # Setup file upload mocking
        mock_upload.return_value = None
        mock_get_uri.return_value = f"test://dataset-uri-{base_workload_id}"

        # Setup DMS client mocking
        mock_dms_instance = mock_dms.return_value
        mock_dms_instance.is_deployed.return_value = False
        mock_dms_instance.deploy_model.return_value = None
        mock_dms_instance.wait_for_deployment.return_value = None
        mock_dms_instance.shutdown_deployment.return_value = None

        # Setup embedding mocking
        def mock_get_embedding(queries, input_type="query"):
            """Mock get_embedding to return appropriate number of embeddings"""
            if isinstance(queries, list):
                # Return one embedding per query
                return [[0.1] * 2048 for _ in queries]  # Mock 2048-dim embeddings
            else:
                # Single query
                return [[0.1] * 2048]  # Single mock 2048-dim embedding

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.get_embedding.side_effect = mock_get_embedding
        mock_embedding_instance.get_embeddings_batch.side_effect = mock_get_embedding
        mock_embedding_class.return_value = mock_embedding_instance
        mock_embedding_class_icl.return_value = mock_embedding_instance

        # Setup Elasticsearch embeddings mocking
        mock_index_embeddings.return_value = "test-embeddings-index"
        mock_index_embeddings_icl.return_value = "test-embeddings-index"
        # Mock search_similar_embeddings to return list of (score, tool_name, record) tuples
        mock_record = {
            "request": {"messages": [{"role": "user", "content": "Test question"}]},
            "response": {"choices": [{"message": {"content": "Test response"}}]},
        }
        mock_search_embeddings.return_value = [(0.9, "no_tool", mock_record)]
        mock_search_embeddings_icl.return_value = [(0.9, "no_tool", mock_record)]
        mock_delete_embeddings.return_value = None

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
                DataSplitConfig,
                ICLConfig,
                LLMJobConfig,
                NIMConfig,
                TrainingConfig,
            )

            # Create a proper mock LLM job config
            mock_llm_job = LLMJobConfig(
                nims=[
                    NIMConfig(
                        model_name="nim/test-model",  # Use original vendor prefix
                        context_length=8192,
                        customization_enabled=True,
                        customizer_configs=CustomizerConfig(
                            gpus=1,
                            max_seq_length=2048,
                            num_nodes=1,
                            target=CustomizerTarget(
                                name="test-target",
                                target_type="nemo",
                                description="Test target for customization",
                                enabled=True,
                                base_model="test-model",
                                model_uri="",
                                tokenizer={},
                                num_parameters=1000000000,
                                precision="bf16-mixed",
                            ),
                        ),
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
            from src.config import LoRAConfig

            sample_customizer = CustomizerConfig(
                gpus=1,
                num_nodes=1,
                max_seq_length=2048,
                target=CustomizerTarget(
                    name="test-target",
                    target_type="nemo",
                    description="Test target for customization",
                    enabled=True,
                    base_model="nim/test-model",
                    model_uri="",
                    tokenizer={},
                    num_parameters=1000000000,
                    precision="bf16-mixed",
                ),
            )

            # Create NIM configuration
            nim_config = NIMConfig(
                model_name="nim/test-model",
                context_length=8192,
                customization_enabled=True,
                customizer_configs=sample_customizer,
            )

            # Create LLM configuration with the NIM nested inside
            mock_llm_config = LLMJobConfig(
                nims=[nim_config],
                data_split_config=DataSplitConfig(
                    eval_size=5,
                    val_ratio=0.1,
                    min_total_records=1,
                    random_seed=42,
                    limit=100,
                    parse_function_arguments=True,
                ),
                icl_config=ICLConfig(
                    max_context_length=8192,
                    reserved_tokens=2048,
                    max_examples=3,
                    min_examples=1,
                    example_selection="uniform_distribution",
                ),
                training_config=TrainingConfig(
                    training_type="sft",
                    finetuning_type="lora",
                    epochs=2,
                    batch_size=16,
                    learning_rate=0.0001,
                    lora=LoRAConfig(adapter_dim=32, adapter_dropout=0.1),
                ),
            )

            # Set the llm_config on mock_settings instead of nims directly
            mock_settings.system_config.llm_judge_config = mock_llm_config

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
                job_config=job_config.model_dump(),
                data_split_config=job_config.data_split_config.model_dump(),
            )

        # Ensure task_result is TaskResult object
        task_result = ensure_task_result(task_result)

        # Adjust validation requirements for tool-calling tests
        if workload_type == "tool_calling":
            # Use custom split config with lower requirements for tool-calling data
            from src.config import DataSplitConfig

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within 20 records
                val_ratio=0.1,
                min_total_records=10,  # Lower minimum for tool-calling due to deduplication
                random_seed=42,  # Set specific seed for consistent behavior
                limit=1000,
                parse_function_arguments=True,
            )
            task_result.data_split_config = custom_split_config
        else:
            # Use custom split config for generic data as well
            from src.config import DataSplitConfig

            custom_split_config = DataSplitConfig(
                eval_size=10,  # Lower eval size to fit within 20 records
                val_ratio=0.1,
                min_total_records=20,  # Lower minimum
                random_seed=42,  # Keep stratified splitting for generic data
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

        # Add nim config to task result for customization
        from src.config import NIMConfig

        task_result.nim = NIMConfig(
            model_name="nim/test-model",  # Use original vendor prefix
            context_length=8192,
            customization_enabled=True,
            customizer_configs=sample_customizer,
        )

        # Return comprehensive environment info
        environment_info = {
            "flywheel_run_id": str(flywheel_run_id),
            "nim_id": nim_docs[0]["_id"],
            "model_name": "nim/test-model",  # Use original vendor prefix
            "customization_enabled": True,
            "task_result": task_result,
            "test_records": [],  # Data is now pre-loaded, so no need to return it here
            "num_records": 0,  # Data is now pre-loaded, so no need to return it here
            "workload_type": workload_type,
            "workload_id": base_workload_id,
            "client_id": base_client_id,
            "mocks": {
                "customizer_client": mock_customizer_client,
                "customization_jobs": mock_customization_jobs,
                "sleep": mock_sleep,
                "dms_client": mock_dms_client,
                "dms_deployments": mock_dms_deployments,
                "upload": mock_upload,
                "dms": mock_dms,
                "embedding": mock_embedding_instance,
            },
        }

        yield environment_info

        # Scope cleanup to THIS test's flywheel_run_id so we do not stomp on
        # collections being mutated concurrently by other pytest processes
        # sharing the same MongoDB instance.
        nim_ids = [d["_id"] for d in mongo_db.nims.find({"flywheel_run_id": flywheel_run_id})]
        if nim_ids:
            mongo_db.customizations.delete_many({"nim_id": {"$in": nim_ids}})
        mongo_db.nims.delete_many({"flywheel_run_id": flywheel_run_id})
        mongo_db.flywheel_runs.delete_many({"_id": flywheel_run_id})
        mongo_db.llm_judge_runs.delete_many({"flywheel_run_id": flywheel_run_id})


@pytest.mark.integration
@pytest.mark.customization
class TestCustomizationWorkflows:
    """Test customization workflows with real database dependencies."""

    def test_complete_customization_workflow(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test complete customization workflow with real data flow."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Execute spin up NIM
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))
        assert nim_result.error is None
        assert nim_result.nim is not None

        custom_result = ensure_task_result(start_customization(nim_result))
        assert custom_result.error is None
        assert custom_result.customization is not None
        assert custom_result.customization.model_name is not None
        assert custom_result.customization.percent_done == 100.0

        # Execute shutdown deployment
        shutdown_result = ensure_task_result(shutdown_deployment(custom_result))
        assert shutdown_result.error is None

        # Execute finalize
        finalize_result = ensure_task_result(finalize_flywheel_run(shutdown_result))
        assert finalize_result.error is None

        # Verify database state
        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 1

        customization_doc = customizations[0]
        assert customization_doc["progress"] == 100.0
        # In NMP 26.03 the customized model name is f"{workspace}/{output_model_name}".
        # We patch generate_output_model_name to "customized-test-model-N" so the model
        # path becomes "<workspace>/customized-test-model-N".
        from src.config import settings as _s

        workspace = _s.system_config.nmp_config.workspace
        assert customization_doc["customized_model"] == f"{workspace}/customized-test-model-1"
        assert customization_doc["job_id"] is not None

    def test_customization_progress_tracking(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization progress tracking and database updates."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Reuse existing customization jobs mock but add progress tracking behavior
        mock_customization_jobs = env_info["mocks"]["customization_jobs"]

        # Save original behavior
        original_status = mock_customization_jobs.get_status.side_effect

        # 26.03 vocabulary: "active"→"running", "error"→"failed" via _to_legacy_status.
        # progress_pct / epoch are read from nested steps[].tasks[].status_details.
        progress_updates = [
            ("active", 25, 1),
            ("active", 50, 2),
            ("active", 75, 2),
            ("completed", 100, 3),
        ]

        progress_counter = 0

        def mock_status_progress(**kwargs):
            nonlocal progress_counter
            if progress_counter < len(progress_updates):
                status_str, pct, epoch = progress_updates[progress_counter]
                progress_counter += 1
            else:
                status_str, pct, epoch = progress_updates[-1]
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": status_str,
                "steps": [
                    {
                        "status": status_str,
                        "tasks": [{"status_details": {"progress_pct": pct, "epoch": epoch}}],
                    }
                ],
            }
            return mock_status

        mock_customization_jobs.get_status.side_effect = mock_status_progress

        try:
            # Execute spin up NIM
            nim_config = task_result.nim.model_dump()
            nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

            custom_result = ensure_task_result(start_customization(nim_result))
            assert custom_result.error is None
            assert custom_result.customization is not None

            # Verify progress was tracked in database
            customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
            assert len(customizations) == 1

            customization_doc = customizations[0]
            assert customization_doc["progress"] == 100.0
            assert customization_doc["epochs_completed"] == 3
        finally:
            # Restore original mock behavior
            mock_customization_jobs.get_status.side_effect = original_status

    def test_customization_cancellation_handling(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization cancellation handling and database state updates."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Cancel the flywheel run using the proper function
        cancel_job_resources(env_info["flywheel_run_id"])

        # Execute spin up NIM
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

        # Execute customization (should be cancelled)
        result = ensure_task_result(start_customization(nim_result))
        assert result.error is not None
        assert "Flywheel run cancelled" in result.error

        # Verify no customization records created due to cancellation
        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 0

    def test_customization_skip_on_previous_error(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization skipping when previous task has error."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Execute spin up NIM
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

        # Add previous error to task result after spin up (since spin_up_nim clears errors)
        nim_result.error = "Previous task failed"

        # Execute customization (should be skipped)
        result = ensure_task_result(start_customization(nim_result))
        assert result.error == "Previous task failed"

        # Verify no customization records created
        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 0

    def test_customization_disabled_skip(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization skipping when customization is disabled."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Disable customization for this NIM
        task_result.nim.customization_enabled = False

        # Execute spin up NIM
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

        # Execute customization (should be skipped)
        result = ensure_task_result(start_customization(nim_result))
        assert result.error is None
        assert result.customization is None

        # Verify no customization records created
        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 0


@pytest.mark.integration
@pytest.mark.customization
class TestCustomizationErrorHandling:
    """Test customization error handling and rollback scenarios."""

    def test_customization_job_failure_handling(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test handling of customization job failures."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Reuse existing customization jobs mock but modify behavior to simulate failure
        mock_customization_jobs = env_info["mocks"]["customization_jobs"]

        # Save original behavior
        original_create = mock_customization_jobs.create.side_effect
        original_status = mock_customization_jobs.get_status.side_effect

        # Update the mock behavior to simulate failure
        def mock_job_create_failure(**kwargs):
            raise Exception("Internal server error")

        def mock_job_status_failure(**kwargs):
            # 26.03: "error" is the failure status; _to_legacy_status maps it to "failed".
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": "error",
                "steps": [],
            }
            return mock_status

        mock_customization_jobs.create.side_effect = mock_job_create_failure
        mock_customization_jobs.get_status.side_effect = mock_job_status_failure

        try:
            # Execute spin up NIM
            nim_config = task_result.nim.model_dump()
            nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

            # Execute customization (should fail)
            result = ensure_task_result(start_customization(nim_result))
            assert result.error is not None
            print(f"#####################{result.error}")
            assert "Failed to start training job" in result.error
            assert "Internal server error" in result.error

            # Verify error was recorded in database
            customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
            assert len(customizations) == 1

            customization_doc = customizations[0]
            assert customization_doc["progress"] == 0.0
            assert "error" in customization_doc
            assert customization_doc["finished_at"] is not None
        finally:
            # Restore original mock behavior
            mock_customization_jobs.create.side_effect = original_create
            mock_customization_jobs.get_status.side_effect = original_status

    def test_customization_timeout_handling(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization timeout handling."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Reuse existing customization jobs mock but modify behavior to simulate timeout
        mock_customization_jobs = env_info["mocks"]["customization_jobs"]

        # Save original behavior
        original_status = mock_customization_jobs.get_status.side_effect

        # Update mock behavior to always return active status (timeout simulation)
        # 26.03: "active" maps to legacy "running".
        def mock_job_status_timeout(**kwargs):
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {
                "status": "active",
                "steps": [
                    {
                        "status": "active",
                        "tasks": [{"status_details": {"progress_pct": 50, "epoch": 1}}],
                    }
                ],
            }
            return mock_status

        mock_customization_jobs.get_status.side_effect = mock_job_status_timeout

        try:
            # Execute spin up NIM
            nim_config = task_result.nim.model_dump()
            nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

            # Execute customization with timeout simulation
            with patch(
                "src.backends.nmp.customizer.Customizer.wait_for_customization"
            ) as mock_wait:
                mock_wait.side_effect = TimeoutError("Customization timed out")

                result = ensure_task_result(start_customization(nim_result))
                assert result.error is not None
                assert "Customization timed out" in result.error
        finally:
            # Restore original mock behavior
            mock_customization_jobs.get_status.side_effect = original_status

    def test_customization_rollback_on_error(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization rollback when errors occur."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Reuse existing customization jobs mock but modify behavior to simulate rollback
        mock_customization_jobs = env_info["mocks"]["customization_jobs"]

        # Save original behavior
        original_create = mock_customization_jobs.create.side_effect
        original_status = mock_customization_jobs.get_status.side_effect

        job_created = False

        # Update the mock behavior to simulate rollback scenario.
        # 26.03 jobs.create returns an object with ``.name`` (not ``.id``).
        def mock_job_create_rollback(**kwargs):
            nonlocal job_created
            job_created = True
            mock_job = MagicMock()
            mock_job.name = "fail-job-123"
            return mock_job

        def mock_job_status_rollback(**kwargs):
            if job_created:
                raise Exception("Network error during customization")
            mock_status = MagicMock()
            mock_status.model_dump.return_value = {"status": "active", "steps": []}
            return mock_status

        mock_customization_jobs.create.side_effect = mock_job_create_rollback
        mock_customization_jobs.get_status.side_effect = mock_job_status_rollback

        try:
            # Execute spin up NIM
            nim_config = task_result.nim.model_dump()
            nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

            # Execute customization (should fail and rollback)
            result = ensure_task_result(start_customization(nim_result))
            assert result.error is not None
            assert "Network error during customization" in result.error

            # Verify rollback occurred
            customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
            assert len(customizations) == 1

            customization_doc = customizations[0]
            assert customization_doc["progress"] == 0.0
            assert "error" in customization_doc
        finally:
            # Restore original mock behavior
            mock_customization_jobs.create.side_effect = original_create
            mock_customization_jobs.get_status.side_effect = original_status


@pytest.mark.integration
@pytest.mark.customization
class TestCustomizationCleanup:
    """Test customization cleanup and resource management."""

    def test_customization_cleanup_on_success(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test proper cleanup after successful customization."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Execute complete workflow
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))
        custom_result = ensure_task_result(start_customization(nim_result))
        shutdown_result = ensure_task_result(shutdown_deployment(custom_result))
        finalize_result = ensure_task_result(finalize_flywheel_run(shutdown_result))

        # Verify all components completed successfully
        assert finalize_result.error is None

        # Verify database state is clean
        flywheel_runs = list(
            mongo_db.flywheel_runs.find({"_id": ObjectId(env_info["flywheel_run_id"])})
        )
        assert len(flywheel_runs) == 1
        assert flywheel_runs[0]["status"] == "completed"

        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 1
        assert customizations[0]["progress"] == 100.0

    def test_customization_cleanup_on_failure(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test proper cleanup after customization failure."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Use existing mocks from environment
        mock_customization_jobs = env_info["mocks"]["customization_jobs"]
        mock_sleep = env_info["mocks"]["sleep"]

        # Mock customization to fail after job creation but before completion
        with patch("src.backends.nmp.customizer.Customizer.cancel_job") as mock_cancel_job:
            job_created = False

            # Save original mock behavior
            original_create = mock_customization_jobs.create.side_effect
            original_status = mock_customization_jobs.get_status.side_effect

            def mock_job_create_failure(**kwargs):
                nonlocal job_created
                job_created = True
                # 26.03: jobs.create returns object keyed by ``name`` (not ``id``).
                mock_job = MagicMock()
                mock_job.name = "customization-job-123"
                return mock_job

            def mock_job_status_failure(**kwargs):
                if job_created:
                    # Simulate job failure after creation
                    raise Exception("Customization job failed during execution")
                mock_status = MagicMock()
                mock_status.model_dump.return_value = {"status": "active", "steps": []}
                return mock_status

            # Update mock behavior
            mock_customization_jobs.create.side_effect = mock_job_create_failure
            mock_customization_jobs.get_status.side_effect = mock_job_status_failure
            mock_sleep.return_value = None
            mock_cancel_job.return_value = None

            try:
                # Execute workflow with failure
                nim_config = task_result.nim.model_dump()
                nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))
                custom_result = ensure_task_result(start_customization(nim_result))
                shutdown_result = ensure_task_result(shutdown_deployment(custom_result))
                finalize_result = ensure_task_result(finalize_flywheel_run(shutdown_result))

                # Verify error was properly handled
                assert custom_result.error is not None
                assert "Customization job failed during execution" in custom_result.error
                assert finalize_result.error is not None  # Finalize should propagate the error

                # Verify cleanup operations were performed
                # 1. Verify the job was cancelled (if job was created)
                if job_created:
                    mock_cancel_job.assert_called_once_with("customization-job-123")

                    # 2. Verify database state reflects the failure
                    customizations = list(
                        mongo_db.customizations.find({"nim_id": env_info["nim_id"]})
                    )
                    assert len(customizations) == 1
                    assert customizations[0]["progress"] == 0.0
                    assert "error" in customizations[0]
            finally:
                # Restore original mock behavior
                mock_customization_jobs.create.side_effect = original_create
                mock_customization_jobs.get_status.side_effect = original_status

    def test_customization_cancellation_cleanup(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test cleanup when customization is cancelled."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Cancel the flywheel run using the proper function
        cancel_job_resources(env_info["flywheel_run_id"])

        # Execute workflow with cancellation
        nim_config = task_result.nim.model_dump()
        nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))
        custom_result = ensure_task_result(start_customization(nim_result))
        shutdown_result = ensure_task_result(shutdown_deployment(custom_result))
        ensure_task_result(finalize_flywheel_run(shutdown_result))

        # Verify cancellation was handled properly
        assert custom_result.error is not None
        assert "Flywheel run cancelled" in custom_result.error

        # Verify no customization records were created
        customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
        assert len(customizations) == 0

    def test_customization_cancellation_during_model_sync(
        self,
        customization_environment,
        mongo_db,
    ):
        """Test customization cancellation during wait_for_model_sync phase."""
        env_info = customization_environment
        task_result = env_info["task_result"]

        # Use existing mocks from environment
        mock_sleep = env_info["mocks"]["sleep"]

        # Mock customization to succeed but then cancel during model sync
        with (
            patch("src.backends.nmp.customizer.Customizer.cancel_job") as mock_cancel_job,
            patch("src.backends.nmp.customizer.Customizer.wait_for_model_sync") as mock_wait_sync,
        ):

            def mock_post_request(url, **kwargs):
                url_str = str(url)
                if "/v1/customization/configs" in url_str:
                    return MagicMock(status_code=200, json=lambda: {"name": "test-config"})
                elif "/v1/customization/jobs" in url_str:
                    return MagicMock(
                        status_code=200,
                        json=lambda: {
                            "id": "customization-job-456",
                            "output_model": "customized-test-model",
                        },
                    )
                else:
                    raise ValueError(f"Unexpected POST request: {url}")

            def mock_get_request(url, **kwargs):
                url_str = str(url)
                if "/v1/customization/jobs/" in url_str and "/status" in url_str:
                    return MagicMock(
                        status_code=200,
                        json=lambda: {
                            "status": "completed",
                            "percentage_done": 100,
                            "epochs_completed": 3,
                        },
                    )
                elif "/v1/models" in url_str:
                    return MagicMock(
                        status_code=200,
                        json=lambda: {
                            "data": [
                                {"id": "nim/test-model"},  # Use original vendor prefix
                                # Note: customized-test-model is NOT in the list to simulate sync timeout
                            ]
                        },
                    )
                else:
                    raise ValueError(f"Unexpected GET request: {url}")

            # Mock wait_for_model_sync to simulate cancellation during sync
            def mock_wait_for_model_sync(
                customized_model, flywheel_run_id, check_interval=30, timeout=3600
            ):
                # Simulate cancellation during model sync
                raise Exception(
                    "Flywheel run cancelled: Flywheel run was cancelled during model sync"
                )

            # Mock requests are handled by NemoMicroservices client mocking
            mock_sleep.return_value = None
            mock_cancel_job.return_value = None
            mock_wait_sync.side_effect = mock_wait_for_model_sync

            # Execute spin up NIM
            nim_config = task_result.nim.model_dump()
            nim_result = ensure_task_result(spin_up_nim(task_result, nim_config))

            # Execute customization (should fail during model sync)
            result = ensure_task_result(start_customization(nim_result))
            assert result.error is not None
            assert "Flywheel run cancelled" in result.error

            # Verify cleanup operations were performed
            # 1. Verify the job was cancelled (check that it was called with any job ID)
            mock_cancel_job.assert_called_once()
            # Verify the job ID starts with expected pattern
            call_args = mock_cancel_job.call_args[0][0]
            assert call_args.startswith("customization-job-")

            # 2. Verify database state reflects the cancellation
            customizations = list(mongo_db.customizations.find({"nim_id": env_info["nim_id"]}))
            assert len(customizations) == 1
            assert customizations[0]["progress"] == 0.0
            assert "error" in customizations[0]
            assert "Flywheel run cancelled" in customizations[0]["error"]
