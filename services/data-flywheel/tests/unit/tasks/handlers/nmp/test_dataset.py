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

"""Tests for data management tasks."""

from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import DatasetType, TaskResult, WorkloadClassification
from src.config import DataSplitConfig, EmbeddingConfig, ICLConfig, SimilarityConfig
from src.lib.flywheel.cancellation import FlywheelCancelledError
from src.tasks.tasks import create_datasets
from tests.unit.tasks.conftest import (
    convert_result_to_task_result,
    get_default_job_config,
)


@pytest.fixture
def mock_es_client():
    """Fixture to mock Elasticsearch client."""
    mock_instance = MagicMock()

    # Mock only the methods actually used in failing test paths
    mock_instance.search.return_value = {"_scroll_id": "scroll123", "hits": {"hits": []}}
    mock_instance.ping.return_value = True
    mock_instance.cluster.health.return_value = {"status": "green"}
    mock_instance.indices.exists.return_value = False
    mock_instance.indices.create.return_value = {}
    mock_instance.indices.refresh.return_value = {}
    mock_instance.indices.delete.return_value = {}

    # Patch all the specific import locations where get_es_client is used
    with (
        patch("src.lib.integration.es_client.get_es_client", return_value=mock_instance),
        patch("src.lib.integration.record_exporter.get_es_client", return_value=mock_instance),
        patch("src.lib.flywheel.icl_selection.get_es_client", return_value=mock_instance),
        patch("src.lib.integration.es_client.index_embeddings_to_es", return_value="test_index"),
        patch("src.lib.integration.es_client.search_similar_embeddings", return_value=[]),
        patch("src.lib.integration.es_client.delete_embeddings_index", return_value=None),
        patch("src.lib.flywheel.icl_selection.search_similar_embeddings", return_value=[]),
        patch("src.lib.flywheel.icl_selection.index_embeddings_to_es", return_value="test_index"),
    ):
        yield mock_instance


@pytest.fixture
def mock_embedding_client():
    """Fixture to mock Embedding client."""

    def mock_get_embeddings_batch(queries, input_type="query"):
        """Mock get_embeddings_batch to return appropriate number of embeddings"""
        if isinstance(queries, list):
            # Return one embedding per query
            return [[0.1, 0.2, 0.3] * 682 for _ in queries]  # Mock 2048-dim embeddings
        else:
            # Single query
            return [[0.1, 0.2, 0.3] * 682]  # Single mock 2048-dim embedding

    with (
        patch("src.backends.nmp.provider.NmpBackendProvider.embedding") as mock_embedding_class,
        patch("src.backends.nmp.embedding.Embedding") as mock_embedding_class_nemo,
    ):
        mock_instance = MagicMock()
        mock_instance.get_embeddings_batch.side_effect = mock_get_embeddings_batch
        mock_embedding_class.return_value = mock_instance
        mock_embedding_class_nemo.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_data_uploader():
    """Fixture to mock DataUploader."""
    with patch("src.backends.nmp.provider.NmpBackendProvider.datastore") as mock:
        mock_instance = MagicMock()
        # Ensure that `get_file_uri` (used when recording dataset metadata) returns a
        # plain string.  A raw ``MagicMock`` instance cannot be encoded by BSON and
        # causes an ``InvalidDocument`` error when the code under test attempts to
        # update MongoDB.
        mock_instance.get_file_uri.return_value = "nmp://test-namespace/datasets/dummy.jsonl"
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def split_config():
    """Fixture to provide a consistent DataSplitConfig for tests."""
    from src.config import DataSplitConfig

    return DataSplitConfig(eval_size=2, limit=5, min_total_records=5)


@pytest.fixture
def icl_config():
    """Fixture to provide a consistent ICLConfig for tests."""
    from src.config import ICLConfig

    return ICLConfig(max_examples=2, min_examples=1, example_selection="uniform_distribution")


@pytest.fixture
def job_config(split_config, icl_config):
    """Fixture to provide a consistent job config for tests."""
    from src.config import settings

    return settings.job_configs["llm_job"].model_copy(
        update={"data_split_config": split_config, "icl_config": icl_config}
    )


class TestDatasetCreationBasic:
    """Tests for basic dataset creation functionality."""

    def test_create_datasets(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test creating datasets from Elasticsearch data."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        # Adjust the previous_result data_split_config to match the sample data size
        previous_result.data_split_config = DataSplitConfig(
            limit=5, min_total_records=1, eval_size=1
        )

        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": f"Question {i}"},
                                    {"role": "assistant", "content": f"Answer {i}"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                        }
                    }
                    for i in range(5)
                ]
                + [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": "What are transformers?"},
                                    {"role": "assistant", "content": "Transformers are..."},
                                ]
                            },
                            "response": {
                                "choices": [{"message": {"content": "Transformer architecture..."}}]
                            },
                        }
                    },
                ]
            },
        }

        result = convert_result_to_task_result(create_datasets(previous_result))

        assert isinstance(result, TaskResult)
        assert result.workload_id == workload_id
        assert result.client_id == client_id
        assert result.flywheel_run_id == flywheel_run_id
        assert result.datasets is not None
        assert len(result.datasets) > 0

        mock_es_client.search.assert_called_once()
        assert mock_data_uploader.upload_data.call_count >= 1

    def test_create_datasets_with_semantic_similarity_success(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test the end-to-end flow of create_datasets with ICL semantic similarity."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        # Configure settings for semantic similarity
        embedding_config = EmbeddingConfig(
            deployment_type="remote",
            model_name="test_embedding_model",
            url="http://fake-url",
            api_key="fake-key",
        )
        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)
        icl_config = ICLConfig(
            example_selection="semantic_similarity", similarity_config=similarity_config
        )
        job_config = get_default_job_config()
        job_config["icl_config"] = icl_config

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        # Mock ES to return records for the initial pull
        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": f"Question {i}"},
                                    {"role": "assistant", "content": f"Answer {i}"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                        }
                    }
                    for i in range(20)  # Provide enough for train/eval split
                ]
            },
        }

        # Mock the specific external-facing functions within the ICL workflow
        with (
            patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index_embeddings,
            patch(
                "src.lib.flywheel.icl_selection.search_similar_embeddings"
            ) as mock_search_embeddings,
        ):
            # Mock search to return a list of (id, score, record) tuples
            mock_search_embeddings.return_value = [
                (
                    "id1",
                    0.9,
                    {
                        "request": {"messages": [{"role": "user", "content": "similar_question"}]},
                        "response": {"choices": [{"message": {"content": "similar_answer"}}]},
                    },
                )
            ]

            result = convert_result_to_task_result(create_datasets(previous_result))

            # --- Assertions ---
            # Verify the ICL selection external calls were made
            mock_embedding_client.get_embeddings_batch.assert_called()
            mock_index_embeddings.assert_called_once()
            mock_search_embeddings.assert_called()

            # Verify the final result is correctly updated
            assert result.datasets is not None
            assert DatasetType.ICL in result.datasets

            # Verify data uploader was called for the ICL dataset (and others)
            assert mock_data_uploader.upload_data.call_count >= 1


class TestDatasetCreationConfiguration:
    """Tests for dataset creation with custom configurations."""

    def test_create_datasets_with_custom_data_split_config(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        icl_config,
    ):
        """Test create_datasets with custom data split config."""

        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        # Create a real data split config
        custom_split_config = DataSplitConfig(
            min_total_records=10, random_seed=123, eval_size=5, val_ratio=0.2, limit=10
        )
        job_config = get_default_job_config()
        job_config["data_split_config"] = custom_split_config

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=custom_split_config,
            job_config=job_config,
        )

        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": f"Question {i}"},
                                    {"role": "assistant", "content": f"Answer {i}"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                        }
                    }
                    for i in range(5)
                ]
            },
        }

        with (
            patch("src.tasks.handlers.nmp.dataset.RecordExporter") as mock_record_exporter_class,
            patch(
                "src.tasks.handlers.nmp.dataset.identify_workload_type"
            ) as mock_identify_workload,
            patch("src.tasks.handlers.nmp.dataset.DatasetCreator") as mock_dataset_creator_class,
            patch(
                "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
            ) as mock_check_cancellation,
        ):
            mock_record_exporter = mock_record_exporter_class.return_value
            mock_record_exporter.get_records.return_value = ["record1", "record2"]

            mock_check_cancellation.return_value = None
            mock_identify_workload.return_value = WorkloadClassification.GENERIC

            mock_dataset_creator = mock_dataset_creator_class.return_value
            mock_dataset_creator.create_datasets.return_value = (None, {"base": "test-dataset"})

            convert_result_to_task_result(create_datasets(previous_result))

            # Verify custom split config was used
            mock_record_exporter.get_records.assert_called_once_with(
                client_id, workload_id, custom_split_config
            )

            # Verify DatasetCreator was called with custom split config
            mock_dataset_creator_class.assert_called_once_with(
                ["record1", "record2"],
                flywheel_run_id,
                "",
                workload_id,
                client_id,
                split_config=custom_split_config,
                icl_config=ICLConfig(**get_default_job_config()["icl_config"]),
            )

    def test_create_datasets_celery_serialization_dict_handling(
        self, split_config, icl_config, job_config
    ):
        """Test create_datasets with dict input to cover line 195 (Celery serialization)."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        # Pass a dict instead of TaskResult to test Celery serialization handling (line 195)
        previous_result_dict = {
            "workload_id": workload_id,
            "flywheel_run_id": flywheel_run_id,
            "client_id": client_id,
            "data_split_config": split_config,
            "job_config": job_config,
            "job_type": "llm_job",
        }

        with (
            patch("src.tasks.handlers.nmp.dataset.RecordExporter") as mock_record_exporter_class,
            patch(
                "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
            ) as mock_check_cancellation,
            patch(
                "src.tasks.handlers.nmp.dataset.identify_workload_type"
            ) as mock_identify_workload,
            patch("src.tasks.handlers.nmp.dataset.DatasetCreator") as mock_dataset_creator_class,
        ):
            mock_record_exporter = mock_record_exporter_class.return_value
            mock_record_exporter.get_records.return_value = ["record1", "record2"]

            mock_check_cancellation.return_value = None
            mock_identify_workload.return_value = WorkloadClassification.GENERIC

            mock_dataset_creator = mock_dataset_creator_class.return_value
            mock_dataset_creator.create_datasets.return_value = (None, {"base": "test-dataset"})

            # This should trigger line 195: if isinstance(previous_result, dict)
            result = convert_result_to_task_result(create_datasets(previous_result_dict))

            assert isinstance(result, TaskResult)
            assert result.workload_id == workload_id
            assert result.client_id == client_id
            assert result.flywheel_run_id == flywheel_run_id

    def test_create_datasets_direct_dict_input(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test create_datasets with direct dict input to specifically cover line 195."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"
        split_config.limit = 5

        # Pass a dict directly to trigger line 195: if isinstance(previous_result, dict)
        previous_result_dict = {
            "workload_id": workload_id,
            "flywheel_run_id": flywheel_run_id,
            "client_id": client_id,
            "data_split_config": split_config,
            "job_config": job_config,
            "job_type": "llm_job",
        }

        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": "Question 1"},
                                    {"role": "assistant", "content": "Answer 1"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": "Response 1"}}]},
                        }
                    }
                ]
            },
        }

        with (
            patch("src.tasks.handlers.nmp.dataset.RecordExporter") as mock_record_exporter_class,
            patch(
                "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
            ) as mock_check_cancellation,
            patch(
                "src.tasks.handlers.nmp.dataset.identify_workload_type"
            ) as mock_identify_workload,
            patch("src.tasks.handlers.nmp.dataset.DatasetCreator") as mock_dataset_creator_class,
        ):
            mock_record_exporter = mock_record_exporter_class.return_value
            mock_record_exporter.get_records.return_value = ["record1"]

            mock_check_cancellation.return_value = None
            mock_identify_workload.return_value = WorkloadClassification.GENERIC

            mock_dataset_creator = mock_dataset_creator_class.return_value
            mock_dataset_creator.create_datasets.return_value = (None, {"base": "test-dataset"})

            # This should trigger line 195: previous_result = TaskResult(**previous_result)
            result = convert_result_to_task_result(create_datasets(previous_result_dict))

            assert isinstance(result, TaskResult)
            assert result.workload_id == workload_id


class TestDatasetCreationErrorHandling:
    """Tests for dataset creation error handling scenarios."""

    def test_create_datasets_empty_data(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test creating datasets with empty Elasticsearch response."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": []  # Empty hits list
            },
        }

        with (
            patch(
                "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
            ) as mock_check_cancellation,
        ):
            mock_check_cancellation.return_value = None

            with pytest.raises(Exception) as exc_info:
                create_datasets(previous_result)

            # The error message now comes from DataValidator instead of RecordExporter
            assert "Not enough records found for the given workload" in str(exc_info.value)

            mock_es_client.search.assert_called_once()
            mock_data_uploader.upload_data.assert_not_called()

    def test_create_datasets_fails_on_icl_error(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test create_datasets fails gracefully when get_embeddings_batch fails."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        # Configure settings for semantic similarity
        embedding_config = EmbeddingConfig(
            deployment_type="remote",
            model_name="test_embedding_model",
            url="http://fake-url",
            api_key="fake-key",
        )
        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)
        icl_config = ICLConfig(
            example_selection="semantic_similarity", similarity_config=similarity_config
        )
        job_config = get_default_job_config()
        job_config["icl_config"] = icl_config

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        # Mock ES to return valid records
        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": f"Question {i}"},
                                    {"role": "assistant", "content": f"Answer {i}"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                        }
                    }
                    for i in range(20)
                ]
            },
        }

        # Configure the mock to raise an exception for this specific test
        mock_embedding_client.get_embeddings_batch.side_effect = Exception(
            "Embedding generation failed"
        )

        with pytest.raises(Exception) as exc_info:
            create_datasets(previous_result)

        assert "Embedding generation failed" in str(exc_info.value)
        mock_embedding_client.get_embeddings_batch.assert_called_once()

    def test_create_datasets_error_handling_unboundlocalerror_expected(
        self,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_task_db,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test create_datasets error handling - UnboundLocalError is expected when RecordExporter fails."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        with (
            patch("src.tasks.handlers.nmp.dataset.RecordExporter") as mock_record_exporter_class,
            patch(
                "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
            ) as mock_check_cancellation,
        ):
            # Make RecordExporter raise an exception
            mock_record_exporter_class.side_effect = Exception("Record export failed")

            # Configure cancellation check to pass (not cancelled)
            mock_check_cancellation.return_value = None

            # This should raise the exception as expected when RecordExporter fails
            with pytest.raises(Exception) as exc_info:
                create_datasets(previous_result)

            assert "Record export failed" in str(exc_info.value)


class TestDatasetCreationCancellation:
    """Tests for dataset creation cancellation scenarios."""

    def test_create_datasets_cancellation(
        self,
        mock_task_db,
        mock_es_client,
        mock_embedding_client,
        mock_data_uploader,
        mock_dms_client,
        split_config,
        icl_config,
        job_config,
    ):
        """Test create_datasets when job is cancelled."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        with patch(
            "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation"
        ) as mock_check_cancellation:
            # Configure cancellation check to raise FlywheelCancelledError
            mock_check_cancellation.side_effect = FlywheelCancelledError(
                flywheel_run_id, "Flywheel run was cancelled"
            )

            with pytest.raises(FlywheelCancelledError):
                create_datasets(previous_result)

            # Verify cancellation was checked
            mock_check_cancellation.assert_called_once_with(flywheel_run_id, raise_error=True)

            # Verify that no data processing occurred after cancellation
            mock_es_client.search.assert_not_called()
            mock_data_uploader.upload_data.assert_not_called()

    def test_create_datasets_cancellation_during_icl_selection(
        self, mock_es_client, mock_dms_client, split_config, icl_config, job_config
    ):
        """Test that cancellation is handled correctly during the ICL selection phase."""
        workload_id = "test-workload"
        flywheel_run_id = str(ObjectId())
        client_id = "test-client"

        # Configure settings for semantic similarity
        embedding_config = EmbeddingConfig(
            deployment_type="remote",
            model_name="test_embedding_model",
            url="http://fake-url",
            api_key="fake-key",
        )
        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)
        icl_config = ICLConfig(
            example_selection="semantic_similarity", similarity_config=similarity_config
        )
        job_config = get_default_job_config()
        job_config["icl_config"] = icl_config

        previous_result = TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            data_split_config=split_config,
            job_config=job_config,
        )

        # Mock ES to return records
        mock_es_client.search.return_value = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {
                                "messages": [
                                    {"role": "user", "content": f"Question {i}"},
                                    {"role": "assistant", "content": f"Answer {i}"},
                                ]
                            },
                            "response": {"choices": [{"message": {"content": f"Response {i}"}}]},
                        }
                    }
                    for i in range(10)
                ]
            },
        }

        with patch(
            "src.tasks.handlers.nmp.dataset.CreateDatasetsHandler.check_cancellation",
            side_effect=FlywheelCancelledError(
                flywheel_run_id, "Cancelled during dataset creation"
            ),
        ) as mock_check_cancellation:
            with pytest.raises(FlywheelCancelledError) as exc_info:
                create_datasets(previous_result)

            assert "Cancelled during dataset creation" in str(exc_info.value)
            mock_check_cancellation.assert_called_once()
