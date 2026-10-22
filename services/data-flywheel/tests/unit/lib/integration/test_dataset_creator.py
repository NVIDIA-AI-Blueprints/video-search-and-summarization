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

import pytest

from src.api.models import WorkloadClassification
from src.config import DataSplitConfig, EmbeddingDataSplitConfig, NMPConfig
from src.lib.flywheel.util import format_training_data
from src.lib.integration.dataset_creator import (
    DatasetCreator,
    DPODatasetCreator,
    EmbeddingDatasetCreator,
)


@pytest.fixture
def mock_nmp_config():
    """Mock NMPConfig for testing."""
    return NMPConfig(
        nemo_base_url="http://test-nemo.com",
        datastore_base_url="http://test-datastore.com",
        namespace="test-namespace",
        hf_token="test-token",
        pvc_size="10Gi",
        registry_base="test-registry",
        customization_enabled=False,
    )


@pytest.fixture
def embedding_split_config():
    """Embedding split configuration for testing."""
    return EmbeddingDataSplitConfig(
        train_split=0.7,
        val_split=0.2,
        eval_split=0.1,
        min_negatives=5,
        max_negatives=10,
        random_seed=42,
    )


@pytest.fixture
def dpo_split_config():
    """DPO split configuration for testing."""
    return DataSplitConfig(
        train_split=0.7,
        val_split=0.2,
        eval_split=0.1,
        eval_size=10,
        min_total_records=20,
        random_seed=42,
    )


class TestDatasetCreator:
    def setup_method(self, mock_nmp_config):
        """Set up test fixtures"""
        self.split_config = DataSplitConfig(train_split=0.7, val_split=0.2, eval_split=0.1)

        self.dataset_creator = DatasetCreator(
            records=[],
            flywheel_run_id="test_run_id",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=self.split_config,
        )

    def test_convert_assistant_content_none_and_empty_string_replacement(self):
        """Test that assistant messages with None and empty string content are converted to single space"""

        # Test data mimicking the real scenario from the user's example
        test_records = [
            {
                "request": {
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a highly intelligent chatbot designed to assist users with queries related to their purchase history. \nUse the necessary tools to fetch relevant information and answer the query based on that information. \nConsider only the current product when processing order status. Ensure that your inputs to the tools include the name of the current product.\nIf there is valid a tool output, provide a proper response to the query based on it. \nAlways rely on tool outputs to generate responses with complete accuracy. Do not hallucinate. \nIf insufficient evidence is available, respond formally stating that there is not enough information to provide an answer.\n\nThe current user id is: 125\nThe current product is: \n",
                        },
                        {
                            "role": "user",
                            "content": "Why was my GeForce Triangulation Tee order canceled?",
                        },
                        {
                            "role": "assistant",
                            "content": None,  # This should be converted to " " to fix NeMo Customizer crash
                            "tool_calls": [
                                {
                                    "id": "chatcmpl-tool-9ba6aef98cbd430cbf4df8ad5ca4fdc5",
                                    "type": "function",
                                    "function": {
                                        "name": "ToOrderStatusAssistant",
                                        "arguments": '{"query": "Why was my GeForce Triangulation Tee order canceled?", "user_id": "125"}',
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "content": "The assistant is now the Order Status Assistant. Reflect on the above conversation between the host assistant and the user. The user's intent is unsatisfied. Use the provided tools to assist the user. Remember, you are Order Status Assistant, and the booking, update, other other action is not complete until after you have successfully invoked the appropriate tool. If the user changes their mind or needs help for other tasks, let the primary host assistant take control. Do not mention who you are - just act as the proxy for the assistant.",
                            "tool_call_id": "chatcmpl-tool-9ba6aef98cbd430cbf4df8ad5ca4fdc5",
                        },
                        {
                            "role": "assistant",
                            "content": "",  # Empty string should also be converted to " " to fix NIM API validation
                        },
                    ],
                    "tools": [
                        {
                            "name": "ToOrderStatusAssistant",
                            "description": "The assistant is now the Order Status Assistant. Reflect on the above conversation between the host assistant and the user. The user's intent is unsatisfied. Use the provided tools to assist the user. Remember, you are Order Status Assistant, and the booking, update, other other action is not complete until after you have successfully invoked the appropriate tool. If the user changes their mind or needs help for other tasks, let the primary host assistant take control. Do not mention who you are - just act as the proxy for the assistant.",
                        }
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,  # For tool-calling workload, this should be converted to " " for accuracy
                                "tool_calls": [
                                    {
                                        "id": "call_456",
                                        "type": "function",
                                        "function": {
                                            "name": "get_order_status",
                                            "arguments": '{"product": "GeForce Triangulation Tee", "user_id": "125"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            }
        ]

        # Test for TOOL_CALLING workload (most comprehensive scenario)
        result = format_training_data(test_records, WorkloadClassification.TOOL_CALLING)

        # Assert that None content in request was converted to single space
        assert len(result) == 1
        assistant_message_with_none = result[0]["messages"][2]
        assert assistant_message_with_none["role"] == "assistant"
        assert assistant_message_with_none["content"] == ""
        assert "tool_calls" in assistant_message_with_none  # Ensure tool_calls preserved

        # Assert that empty string content in request was converted to single space
        assistant_message_with_empty = result[0]["messages"][4]
        assert assistant_message_with_empty["role"] == "assistant"
        assert assistant_message_with_empty["content"] == ""

        # Assert that response content was converted because tool_calls exist
        response_choice = result[0]["messages"][-1]
        assert response_choice["content"] == ""
        assert "tool_calls" in response_choice


class TestEmbeddingDatasetCreator:
    """Test class for EmbeddingDatasetCreator methods."""

    def test_build_relationships(self, embedding_split_config):
        """Test building document relationships graph."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # Test data with relationships
        data = [
            {"query_id": "doc1", "pos_doc_id": "doc2", "neg_doc_ids": ["doc5", "doc6"]},
            {"query_id": "doc2", "pos_doc_id": "doc3", "neg_doc_ids": ["doc7"]},
            {"query_id": "doc3", "pos_doc_id": "doc1", "neg_doc_ids": []},
        ]

        pos_graph, neg_graph, doc_graph = creator.build_relationships(data)

        # Verify positive relationships (bidirectional)
        assert "doc2" in pos_graph["doc1"]
        assert "doc3" in pos_graph["doc2"]
        assert "doc1" in pos_graph["doc3"]

        # Verify bidirectional document graph
        assert "doc2" in doc_graph["doc1"]
        assert "doc1" in doc_graph["doc2"]
        assert "doc3" in doc_graph["doc2"]
        assert "doc2" in doc_graph["doc3"]

        # Verify negative relationships
        assert "doc5" in neg_graph["doc1"]
        assert "doc6" in neg_graph["doc1"]
        assert "doc7" in neg_graph["doc2"]

    def test_find_connected_components_single_cluster(self, embedding_split_config):
        """Test finding connected components with a single cluster."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # All documents are connected
        graph = {
            "doc1": {"doc2", "doc3"},
            "doc2": {"doc1", "doc3"},
            "doc3": {"doc1", "doc2"},
        }

        clusters = creator.find_connected_components(graph)

        assert len(clusters) == 1
        assert clusters[0] == {"doc1", "doc2", "doc3"}

    def test_find_connected_components_multiple_clusters(self, embedding_split_config):
        """Test finding connected components with multiple clusters."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # Two separate clusters
        graph = {
            "doc1": {"doc2"},
            "doc2": {"doc1"},
            "doc3": {"doc4"},
            "doc4": {"doc3"},
        }

        clusters = creator.find_connected_components(graph)

        assert len(clusters) == 2
        cluster_sets = [set(c) for c in clusters]
        assert {"doc1", "doc2"} in cluster_sets
        assert {"doc3", "doc4"} in cluster_sets

    def test_find_connected_components_empty_graph(self, embedding_split_config):
        """Test finding connected components with empty graph."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        clusters = creator.find_connected_components({})
        assert clusters == []

    def test_filter_records(self, embedding_split_config):
        """Test filtering records based on positive and negative graphs."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        pos_graph = {
            "doc1": {"doc2", "doc3"},
            "doc2": {"doc1"},
            "doc4": {"doc5"},  # doc5 not in records, should be filtered
        }

        neg_graph = {
            "doc1": {"doc6", "doc7"},
            "doc2": {"doc8", "doc9"},  # doc9 not in records
        }

        records = [
            {"query_id": "doc1", "query": "query1"},
            {"query_id": "doc2", "query": "query2"},
            {"query_id": "doc3", "query": "query3"},
            {"query_id": "doc4", "query": "query4"},
            {"query_id": "doc6", "query": "query6"},
            {"query_id": "doc8", "query": "query8"},
        ]

        result = creator.filter_records(pos_graph, neg_graph, records)

        # doc1 should have doc2, doc3 as positives (doc3 in records), doc6 as negative (in records)
        assert "doc1" in result
        assert set(result["doc1"]["pos_docs"]) == {"doc2", "doc3"}
        assert set(result["doc1"]["neg_docs"]) == {"doc6"}

        # doc2 should have doc1 as positive, doc8 as negative
        assert "doc2" in result
        assert result["doc2"]["pos_docs"] == ["doc1"]
        assert result["doc2"]["neg_docs"] == ["doc8"]

        # doc4 should be skipped (no valid pos_docs in records)
        assert "doc4" not in result

    def test_build_doc_graph(self, embedding_split_config):
        """Test building document graph from similar records."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        similar_records = [
            {"query_id": "doc1", "pos_doc_id": "doc2", "neg_doc_ids": []},
            {"query_id": "doc2", "pos_doc_id": "doc3", "neg_doc_ids": []},
        ]

        pos_graph, neg_graph, clusters = creator.build_doc_graph(similar_records)

        # Verify we got valid outputs
        assert isinstance(pos_graph, dict)
        assert isinstance(neg_graph, dict)
        assert isinstance(clusters, list)
        assert len(clusters) > 0

    def test_curate_negative_samples_with_predefined_negatives(self, embedding_split_config):
        """Test curating negative samples when negatives are predefined."""
        records = [
            {"query_id": "doc1", "query": "query1"},
            {"query_id": "doc2", "query": "query2"},
            {"query_id": "doc3", "query": "query3"},
            {"query_id": "doc4", "query": "query4"},
            {"query_id": "doc5", "query": "query5"},
        ]

        creator = EmbeddingDatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # Setup test data
        all_docs = {
            "doc1": {
                "pos_docs": ["doc2"],
                "neg_docs": ["doc3", "doc4", "doc5"],  # Predefined negatives
            }
        }

        clusters = [{"doc1", "doc2"}]

        triplets = creator.curate_negative_samples(
            all_docs, clusters, min_negatives=2, max_negatives=5
        )

        assert len(triplets) == 1
        assert triplets[0]["query_id"] == "doc1"
        assert triplets[0]["pos_doc_id"] == "doc2"
        # Should have limited negatives (max 5)
        assert len(triplets[0]["neg_doc_ids"]) == 3

    def test_curate_negative_samples_with_random_negatives(self, embedding_split_config):
        """Test curating negative samples when negatives need to be randomly selected."""
        records = [
            {"query_id": "doc1", "query": "query1"},
            {"query_id": "doc2", "query": "query2"},
            {"query_id": "doc3", "query": "query3"},
            {"query_id": "doc4", "query": "query4"},
            {"query_id": "doc5", "query": "query5"},
            {"query_id": "doc6", "query": "query6"},
            {"query_id": "doc7", "query": "query7"},
        ]

        creator = EmbeddingDatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # Setup test data - no predefined negatives
        all_docs = {
            "doc1": {
                "pos_docs": ["doc2"],
                "neg_docs": [],  # No predefined negatives
            }
        }

        clusters = [{"doc1", "doc2"}]

        triplets = creator.curate_negative_samples(
            all_docs, clusters, min_negatives=2, max_negatives=5
        )

        assert len(triplets) == 1
        assert triplets[0]["query_id"] == "doc1"
        assert triplets[0]["pos_doc_id"] == "doc2"
        # Should have random negatives selected
        neg_count = len(triplets[0]["neg_doc_ids"])
        assert 2 <= neg_count <= 5

    def test_curate_negative_samples_min_greater_than_max_raises_error(
        self, embedding_split_config
    ):
        """Test that min_negatives > max_negatives raises ValueError."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        with pytest.raises(ValueError, match="min_negatives.*cannot be greater than max_negatives"):
            creator.curate_negative_samples({}, [], min_negatives=10, max_negatives=5)

    def test_create_eval_corpus(self, embedding_split_config):
        """Test creating evaluation corpus from eval records."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        eval_records = [
            {
                "query_id": "q1",
                "pos_doc_id": "doc1",
                "pos_doc": "Document 1 text",
                "neg_doc_ids": [
                    {"id": "doc2", "text": "Document 2 text"},
                    {"id": "doc3", "text": "Document 3 text"},
                ],
            },
            {
                "query_id": "q2",
                "pos_doc_id": "doc1",  # Duplicate doc1
                "pos_doc": "Document 1 text",
                "neg_doc_ids": [{"id": "doc4", "text": "Document 4 text"}],
            },
        ]

        corpus = creator.create_eval_corpus(eval_records)

        # Check corpus structure
        assert len(corpus) == 4  # doc1, doc2, doc3, doc4 (doc1 should not be duplicated)

        # Verify corpus entries
        corpus_ids = {entry["_id"] for entry in corpus}
        assert "doc1" in corpus_ids
        assert "doc2" in corpus_ids
        assert "doc3" in corpus_ids
        assert "doc4" in corpus_ids

        # Check structure of an entry
        doc1_entry = next(entry for entry in corpus if entry["_id"] == "doc1")
        assert doc1_entry["title"] == ""
        assert doc1_entry["text"] == "Document 1 text"

    def test_create_eval_queries(self, embedding_split_config):
        """Test creating queries from eval records."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        eval_records = [
            {"query_id": "q1", "query": "What is query 1?"},
            {"query_id": "q2", "query": "What is query 2?"},
            {"query_id": "q3", "query": "What is query 3?"},
        ]

        # Test without filter
        queries = creator.create_eval_queries(eval_records)
        assert len(queries) == 3
        assert queries[0] == {"_id": "q1", "text": "What is query 1?"}

        # Test with filter
        queries_filtered = creator.create_eval_queries(eval_records, ids=["q1", "q3"])
        assert len(queries_filtered) == 2
        query_ids = {q["_id"] for q in queries_filtered}
        assert query_ids == {"q1", "q3"}

    def test_create_eval_qrels(self, embedding_split_config):
        """Test creating qrels from eval records."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        eval_records = [
            {"query_id": "q1", "pos_doc_id": "doc1"},
            {"query_id": "q2", "pos_doc_id": "doc2"},
        ]

        qrels = creator.create_eval_qrels(eval_records)

        assert len(qrels) == 2
        assert qrels[0] == {"query-id": "q1", "corpus-id": "doc1", "score": 1}
        assert qrels[1] == {"query-id": "q2", "corpus-id": "doc2", "score": 1}

    def test_curate_negative_samples_skip_no_cluster(self, embedding_split_config):
        """Test curating negative samples when document has no cluster assignment."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        all_docs = {
            "doc1": {"pos_docs": [], "neg_docs": [], "query": "query1"},
            "doc2": {"pos_docs": [], "neg_docs": [], "query": "query2"},
        }
        # Empty clusters means no documents will have cluster assignments
        clusters = []

        result = creator.curate_negative_samples(
            all_docs, clusters, min_negatives=1, max_negatives=5
        )

        # Should skip all documents without cluster assignments
        assert len(result) == 0

    def test_curate_negative_samples_skip_no_valid_cluster_docs(self, embedding_split_config):
        """Test when cluster has no other valid documents."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        all_docs = {
            "doc1": {"pos_docs": [], "neg_docs": [], "query": "query1"},
        }
        # Cluster with only one document
        clusters = [["doc1"]]

        result = creator.curate_negative_samples(
            all_docs, clusters, min_negatives=1, max_negatives=5
        )

        # Should skip because no other valid documents in cluster
        assert len(result) == 0

    def test_curate_negative_samples_skip_insufficient_negatives(self, embedding_split_config):
        """Test when there aren't enough negative documents available."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        all_docs = {
            "doc1": {"pos_docs": [], "neg_docs": [], "query": "query1"},
            "doc2": {"pos_docs": [], "neg_docs": [], "query": "query2"},
        }
        # Both in same cluster
        clusters = [["doc1", "doc2"]]

        # Request more min_negatives than available documents outside cluster
        result = creator.curate_negative_samples(
            all_docs, clusters, min_negatives=10, max_negatives=15
        )

        # Should skip all because not enough negatives available
        assert len(result) == 0

    def test_curate_negative_samples_exception_handling(self, embedding_split_config):
        """Test exception handling in curate_negative_samples."""
        creator = EmbeddingDatasetCreator(
            records=[],
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=embedding_split_config,
        )

        # Pass invalid data structure to trigger exception
        all_docs = "invalid_data"  # Should be dict
        clusters = []

        with pytest.raises((TypeError, AttributeError)):
            creator.curate_negative_samples(all_docs, clusters, min_negatives=1, max_negatives=5)


class TestDPODatasetCreator:
    """Test class for DPODatasetCreator methods."""

    def test_split_dpo_records(self, dpo_split_config):
        """Test splitting DPO records into eval, train, and val sets."""
        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(50)
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        eval_records, train_records, val_records = creator.split_dpo_records(records)

        # Check that splits exist
        assert len(eval_records) == 10  # eval_size from config
        assert len(train_records) > 0
        assert len(val_records) > 0

        # Check total matches
        assert len(eval_records) + len(train_records) + len(val_records) == 50

        # Verify records don't overlap
        all_prompts = []
        for r in eval_records + train_records + val_records:
            all_prompts.append(r["prompt"][0]["content"])
        assert len(all_prompts) == len(set(all_prompts))

    def test_split_dpo_records_with_seed(self, dpo_split_config):
        """Test that random seed produces consistent splits."""
        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(30)
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        # Split twice with same seed
        eval1, train1, val1 = creator.split_dpo_records(records)
        eval2, train2, val2 = creator.split_dpo_records(records)

        # Should produce same splits
        assert eval1 == eval2
        assert train1 == train2
        assert val1 == val2

    def test_format_dpo_training_data(self, dpo_split_config):
        """Test that DPO training data formatting preserves records."""
        records = [
            {
                "prompt": [{"role": "user", "content": "Question"}],
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            }
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        formatted = creator.format_dpo_training_data(records)

        # NMP 26.03 BinaryPreferenceDataset schema: chosen_response/rejected_response
        # are renamed to chosen/rejected for the customizer's dataset validator.
        assert formatted == [
            {
                "prompt": records[0]["prompt"],
                "chosen": records[0]["chosen_response"],
                "rejected": records[0]["rejected_response"],
            }
        ]

    def test_format_dpo_eval_data(self, dpo_split_config):
        """Test formatting DPO records for evaluation."""
        records = [
            {
                "prompt": [{"role": "user", "content": "Question 1"}],
                "chosen_response": "Good answer 1",
                "rejected_response": "Bad answer 1",
            },
            {
                "prompt": [{"role": "user", "content": "Question 2"}],
                "chosen_response": "Good answer 2",
                "rejected_response": "Bad answer 2",
            },
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        formatted = creator.format_dpo_eval_data(records)

        # Should rename 'prompt' to 'messages'
        assert len(formatted) == 2
        assert "messages" in formatted[0]
        assert "prompt" not in formatted[0]
        assert formatted[0]["messages"] == [{"role": "user", "content": "Question 1"}]
        assert formatted[0]["chosen_response"] == "Good answer 1"
        assert formatted[0]["rejected_response"] == "Bad answer 1"

    def test_deduplicate_dpo_records_with_list_prompts(self, dpo_split_config):
        """Test deduplication of DPO records with list prompts."""
        records = [
            {
                "prompt": [{"role": "user", "content": "Question 1"}],
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            },
            {
                "prompt": [{"role": "user", "content": "Question 1"}],  # Duplicate
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            },
            {
                "prompt": [{"role": "user", "content": "Question 2"}],
                "chosen_response": "Good answer 2",
                "rejected_response": "Bad answer 2",
            },
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        deduplicated = creator._deduplicate_dpo_records(records)

        # Should remove one duplicate
        assert len(deduplicated) == 2

        # Verify unique records remain
        prompts = [r["prompt"][0]["content"] for r in deduplicated]
        assert "Question 1" in prompts
        assert "Question 2" in prompts

    def test_deduplicate_dpo_records_with_string_prompts(self, dpo_split_config):
        """Test deduplication with string prompts."""
        records = [
            {
                "prompt": "Question 1",
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            },
            {
                "prompt": "Question 1",  # Duplicate
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            },
            {
                "prompt": "Question 2",
                "chosen_response": "Good answer 2",
                "rejected_response": "Bad answer 2",
            },
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        deduplicated = creator._deduplicate_dpo_records(records)

        assert len(deduplicated) == 2

    def test_deduplicate_dpo_records_different_responses_not_duplicates(self, dpo_split_config):
        """Test that records with same prompt but different responses are not duplicates."""
        records = [
            {
                "prompt": [{"role": "user", "content": "Question 1"}],
                "chosen_response": "Good answer A",
                "rejected_response": "Bad answer A",
            },
            {
                "prompt": [{"role": "user", "content": "Question 1"}],  # Same prompt
                "chosen_response": "Good answer B",  # Different response
                "rejected_response": "Bad answer B",
            },
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        deduplicated = creator._deduplicate_dpo_records(records)

        # Should keep both since responses differ
        assert len(deduplicated) == 2

    def test_validate_dpo_records_success(self, dpo_split_config):
        """Test successful validation of DPO records."""
        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
                "extra_field": "should be removed",
            }
            for i in range(30)
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        validated = creator._validate_dpo_records(records)

        # Should have all valid records
        assert len(validated) == 30

        # Should only have required fields
        assert "extra_field" not in validated[0]
        assert set(validated[0].keys()) == {"prompt", "chosen_response", "rejected_response"}

    def test_validate_dpo_records_insufficient_records(self, dpo_split_config):
        """Test validation fails with insufficient records."""
        records = [
            {
                "prompt": [{"role": "user", "content": "Question"}],
                "chosen_response": "Good answer",
                "rejected_response": "Bad answer",
            }
        ]  # Only 1 record, need 20

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        with pytest.raises(ValueError, match="Not enough DPO records"):
            creator._validate_dpo_records(records)

    def test_validate_dpo_records_missing_fields(self, dpo_split_config):
        """Test validation handles records with missing fields."""
        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(25)
        ]
        # Add some invalid records
        records.append({"prompt": "Missing responses"})  # Missing chosen/rejected
        records.append({"chosen_response": "Missing prompt", "rejected_response": "Bad"})

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=dpo_split_config,
        )

        validated = creator._validate_dpo_records(records)

        # Should only keep valid records
        assert len(validated) == 25

    def test_validate_dpo_records_with_limit(self):
        """Test validation with limit applied."""
        split_config = DataSplitConfig(
            eval_size=5,
            min_total_records=10,
            limit=15,  # Limit to 15 records
            random_seed=42,
        )

        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(30)  # 30 records available
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=split_config,
        )

        validated = creator._validate_dpo_records(records)

        # Should limit to 15
        assert len(validated) == 15

    def test_validate_dpo_records_eval_size_exceeds_total(self):
        """Test validation fails when eval_size exceeds total records."""
        split_config = DataSplitConfig(
            eval_size=15,  # More than total records
            min_total_records=10,
            random_seed=42,
        )

        records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(12)  # 12 records, but eval_size is 15
        ]

        creator = DPODatasetCreator(
            records=records,
            flywheel_run_id="test_run",
            output_dataset_prefix="test",
            workload_id="test_workload",
            client_id="test_client",
            split_config=split_config,
        )

        with pytest.raises(ValueError, match="eval_size.*cannot be larger"):
            creator._validate_dpo_records(records)
