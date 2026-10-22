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

"""Unit tests for the ICL Selection module."""

from unittest.mock import MagicMock, patch

import pytest

from src.api.models import WorkloadClassification
from src.config import EmbeddingConfig, ICLConfig, SimilarityConfig
from src.lib.flywheel.icl_selection import ICLSelection
from src.lib.flywheel.util import identify_workload_type


class TestICLSelectionDPOMethods:
    """Tests for DPO-specific ICL Selection methods."""

    def test_format_dpo_example_basic(self, icl_selection_factory):
        """Test formatting a basic DPO example."""
        icl_selection = icl_selection_factory("uniform")

        dpo_record = {
            "prompt": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Tell me a joke."},
            ],
            "chosen_response": "Why did the chicken cross the road?",
            "rejected_response": "I don't know any jokes.",
        }

        example_str, token_count = icl_selection.format_dpo_example(dpo_record, idx=1)

        assert "Example 1:" in example_str
        assert "Tell me a joke." in example_str
        assert "✓ PREFERRED Response:" in example_str
        assert "Why did the chicken cross the road?" in example_str
        assert "✗ LESS PREFERRED Response:" in example_str
        assert "I don't know any jokes." in example_str
        assert token_count > 0

    def test_format_dpo_example_with_custom_index(self, icl_selection_factory):
        """Test formatting DPO example with custom index."""
        icl_selection = icl_selection_factory("uniform")

        dpo_record = {
            "prompt": [{"role": "user", "content": "Hello"}],
            "chosen_response": "Hi there!",
            "rejected_response": "What?",
        }

        example_str, token_count = icl_selection.format_dpo_example(dpo_record, idx=5)

        assert "Example 5:" in example_str
        assert token_count > 0

    def test_extract_user_query_dpo_with_prompt(self, icl_selection_factory):
        """Test extracting user query from DPO record with prompt field."""
        icl_selection = icl_selection_factory("uniform")

        record = {
            "prompt": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What's the weather?"},
            ]
        }

        result = icl_selection.extract_user_query_dpo(record)
        assert result == "What's the weather?"

    def test_extract_user_query_dpo_with_messages(self, icl_selection_factory):
        """Test extracting user query from DPO record with messages field."""
        icl_selection = icl_selection_factory("uniform")

        record = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "How are you?"},
            ]
        }

        result = icl_selection.extract_user_query_dpo(record)
        assert result == "How are you?"

    def test_extract_user_query_dpo_no_user_message(self, icl_selection_factory):
        """Test extracting user query when no user message exists."""
        icl_selection = icl_selection_factory("uniform")

        record = {
            "prompt": [
                {"role": "system", "content": "You are helpful."},
            ]
        }

        result = icl_selection.extract_user_query_dpo(record)
        assert result is None

    def test_get_tool_name_dpo_record(self, icl_selection_factory):
        """Test get_tool_name with DPO record (no response field)."""
        icl_selection = icl_selection_factory("uniform")

        # DPO record doesn't have response field
        dpo_record = {
            "prompt": [{"role": "user", "content": "Test"}],
            "chosen_response": "Response",
        }

        result = icl_selection.get_tool_name(dpo_record)
        assert result == "no_tool"


class TestICLSelectionCoreMethods:
    """Tests for core ICL Selection methods and utilities."""

    @pytest.mark.parametrize(
        "text, buffer_percent, mock_tokens, expected_tokens",
        [
            ("This is a test.", 20, 5, 6),
            ("This is a test.", 50, 5, 7),
            ("", 0, 0, 0),
        ],
    )
    def test_estimate_tokens(
        self, icl_selection_factory, text, buffer_percent, mock_tokens, expected_tokens
    ):
        icl_selection = icl_selection_factory("uniform")
        if text:
            with patch("tiktoken.get_encoding") as mock_get_encoding:
                mock_encoding = MagicMock()
                mock_encoding.encode.return_value = list(range(mock_tokens))
                mock_get_encoding.return_value = mock_encoding
                assert (
                    icl_selection.estimate_tokens(text, buffer_percent=buffer_percent)
                    == expected_tokens
                )
        else:
            assert (
                icl_selection.estimate_tokens(text, buffer_percent=buffer_percent)
                == expected_tokens
            )

    def test_estimate_tokens_fallback(self, icl_selection_factory):
        icl_selection = icl_selection_factory("uniform")
        with patch("tiktoken.get_encoding", side_effect=Exception("unavailable")):
            # Falls back to word count approximation
            assert icl_selection.estimate_tokens("A test message!", buffer_percent=20) > 0

    @pytest.mark.parametrize(
        "record_name, expected_substrings",
        [
            ("simple_conversation", ["user: Hello", "assistant: I'm doing well", "For example"]),
            ("with_tool_calls", ["user: What's the weather", "tool calls", "get_weather"]),
        ],
    )
    def test_format_example(
        self, icl_selection_factory, get_record_by_name, record_name, expected_substrings
    ):
        icl_selection = icl_selection_factory("uniform")
        record = get_record_by_name(record_name)
        example_str, token_count = icl_selection.format_example(record)
        for sub in expected_substrings:
            assert sub.lower() in example_str.lower()
        assert token_count > 0

    @pytest.mark.parametrize(
        "tool_calls, expected_name",
        [
            (None, "no_tool"),
            ([], "no_tool"),
            ([{"function": {"name": "get_weather"}}], "get_weather"),
        ],
    )
    def test_get_tool_name(self, icl_selection_factory, tool_calls, expected_name):
        icl_selection = icl_selection_factory("uniform")
        record = {"response": {"choices": [{"message": {"tool_calls": tool_calls}}]}}
        assert icl_selection.get_tool_name(record) == expected_name

    @pytest.mark.parametrize(
        "record, expected_query",
        [
            ({"request": {"messages": [{"role": "user", "content": "Hello"}]}}, "Hello"),
            (
                {
                    "request": {
                        "messages": [
                            {"role": "system", "content": "Sys"},
                            {"role": "user", "content": "Help"},
                        ]
                    }
                },
                "Help",
            ),
            ({"request": {"messages": [{"role": "assistant", "content": "Resp"}]}}, None),
            ({"request": {"messages": []}}, None),
            ({}, None),
        ],
    )
    def test_extract_user_query(self, icl_selection_factory, record, expected_query):
        icl_selection = icl_selection_factory("uniform")
        assert icl_selection.extract_user_query(record) == expected_query

    @pytest.mark.parametrize(
        "existing_system_message, expected_message_count",
        [
            (None, 2),
            ("Existing system message", 2),
        ],
    )
    def test_inject_system_message(
        self, icl_selection_factory, existing_system_message, expected_message_count
    ):
        icl_selection = icl_selection_factory("uniform")
        messages = [{"role": "user", "content": "Hello"}]
        if existing_system_message:
            messages.insert(0, {"role": "system", "content": existing_system_message})
        record = {"request": {"messages": messages}}

        icl_selection._inject_system_message(record, "Example content")

        new_messages = record["request"]["messages"]
        assert len(new_messages) == expected_message_count
        assert new_messages[0]["role"] == "system"
        assert "Example content" in new_messages[0]["content"]
        if existing_system_message:
            assert existing_system_message in new_messages[0]["content"]

    def test_log_injection_stats(self, icl_selection_factory, caplog):
        icl_selection = icl_selection_factory("uniform")
        stats = [(100, 2, 50), (80, 1, 70)]
        icl_selection._log_injection_stats([{}, {}], stats)
        assert "Total ICL Eval Dataset Size: 2" in caplog.text
        assert "On Average Injected 1.5 examples per record" in caplog.text


class TestICLUniformSelection:
    """Tests for uniform distribution-based ICL selection."""

    @pytest.mark.parametrize(
        "total_records, num_tools, expected_bins",
        [
            (10, 5, [2, 2, 2, 2, 2]),
            (10, 3, [4, 3, 3]),
            (2, 5, [1, 1, 0, 0, 0]),
            (10, 1, [10]),
        ],
    )
    def test_uniform_bins(self, icl_selection_factory, total_records, num_tools, expected_bins):
        icl_selection = icl_selection_factory("uniform")
        assert icl_selection.uniform_bins(total_records, num_tools) == expected_bins

    @pytest.mark.parametrize(
        "workload_type, setup_records, expected_groups",
        [
            (WorkloadClassification.GENERIC, lambda r: r, ["generic_examples"]),
            (
                WorkloadClassification.TOOL_CALLING,
                lambda r: [
                    {
                        **rec,
                        "response": {
                            "choices": [
                                {
                                    "message": {
                                        "tool_calls": [{"function": {"name": f"tool_{i % 2}"}}]
                                    }
                                }
                            ]
                        },
                    }
                    for i, rec in enumerate(r)
                ],
                ["tool_0", "tool_1"],
            ),
        ],
    )
    def test_uniform_binning_selection(
        self, icl_selection_factory, sample_records, workload_type, setup_records, expected_groups
    ):
        icl_selection = icl_selection_factory("uniform")
        records = setup_records(sample_records)
        result = icl_selection.uniform_binning_selection(
            records, icl_selection.config, workload_type
        )
        assert all(group in result for group in expected_groups)
        assert (
            "generic_examples" not in result
            if workload_type == WorkloadClassification.TOOL_CALLING
            else True
        )

    def test_uniform_binning_selection_format_failure(self, icl_selection_factory, sample_records):
        icl_selection = icl_selection_factory("uniform")
        with patch.object(icl_selection, "format_example", side_effect=[("", 0), ("Valid", 50)]):
            result = icl_selection.uniform_binning_selection(
                sample_records[:2], icl_selection.config, WorkloadClassification.GENERIC
            )
            assert len(result["generic_examples"]) == 1

    @pytest.mark.parametrize(
        "available_tokens, expected_count", [(0, 0), (50, 0), (100, 1), (300, 3)]
    )
    def test_fit_examples_token_constraints(
        self, icl_selection_factory, available_tokens, expected_count
    ):
        icl_selection = icl_selection_factory("uniform")
        tool_groups = {
            "tool1": [({}, "Ex1", 80), ({}, "Ex2", 120)],
            "tool2": [({}, "Ex3", 90)],
        }
        result = icl_selection.fit_examples_for_record(tool_groups, available_tokens)
        assert len(result) == expected_count

    def test_fit_examples_round_robin(self, icl_selection_factory):
        icl_selection = icl_selection_factory("uniform")
        tool_groups = {
            "tool1": [("r1", "e1", 50), ("r3", "e3", 60)],
            "tool2": [("r2", "e2", 40), ("r4", "e4", 70)],
        }
        result = icl_selection.fit_examples_for_record(tool_groups, 220)
        assert len(result) == 4
        assert [r[0] for r in result] == ["r1", "r2", "r3", "r4"]

    @pytest.mark.parametrize("max_examples", [1, 3])
    def test_generate_icl_records_uniform(
        self, icl_selection_factory, sample_records, max_examples
    ):
        icl_selection = icl_selection_factory("uniform")
        icl_selection.config.max_examples = max_examples
        workload_type = identify_workload_type(sample_records)
        _, result = icl_selection.generate_icl_records(
            sample_records, workload_type, eval_records=sample_records[:max_examples]
        )
        system_content = result[0]["request"]["messages"][0]["content"]
        assert system_content.count("For example") == max_examples

    @pytest.mark.parametrize("empty_input", [[], {}])
    def test_empty_inputs_uniform_selection(self, icl_selection_factory, empty_input):
        icl_selection = icl_selection_factory("uniform")
        if isinstance(empty_input, list):
            result = icl_selection.uniform_binning_selection(
                empty_input, icl_selection.config, WorkloadClassification.GENERIC
            )
            assert result == {}
        elif isinstance(empty_input, dict):
            result = icl_selection.fit_examples_for_record(empty_input, 1000)
            assert result == []


class TestICLEmbeddingSimilarity:
    """Tests for embedding similarity-based ICL selection."""

    @pytest.fixture
    def mock_embedding_client(self):
        with patch("src.backends.nmp.provider.NmpBackendProvider.embedding") as mock_embedding:
            yield mock_embedding.return_value

    @pytest.fixture
    def mock_es_client(self):
        with patch("src.lib.flywheel.icl_selection.get_es_client") as mock_get_es:
            yield mock_get_es.return_value

    def test_embedding_client_initialization(self, mock_es_client):
        embedding_config = EmbeddingConfig(deployment_type="local")
        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)
        config = ICLConfig(
            example_selection="semantic_similarity", similarity_config=similarity_config
        )
        icl_selection = ICLSelection(config, "workload", "client")
        assert hasattr(icl_selection, "embedding_client")
        assert icl_selection.es_client is not None

    def test_embedding_failure_handling(self, mock_es_client, mock_embedding_client):
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        mock_embedding_client.get_embeddings_batch.side_effect = Exception(
            "Embedding service failed"
        )
        with pytest.raises(Exception, match="Embedding service failed"):
            icl_selection._generate_train_embeddings(
                [{"request": {"messages": [{"role": "user", "content": "Test"}]}}]
            )

    @pytest.mark.parametrize(
        "records, queries, expected_binned_data",
        [
            ([], [], {}),
            (
                [
                    {
                        "request": {"messages": [{"role": "user", "content": "Q1"}]},
                        "response": {"choices": [{"message": {"tool_calls": None}}]},
                    }
                ],
                ["Q1"],
                {"no_tool": 1},
            ),
            (
                [
                    {
                        "request": {"messages": [{"role": "user", "content": "Q1"}]},
                        "response": {
                            "choices": [
                                {"message": {"tool_calls": [{"function": {"name": "tool1"}}]}}
                            ]
                        },
                    },
                    {
                        "request": {"messages": [{"role": "user", "content": "Q2"}]},
                        "response": {"choices": [{"message": {"tool_calls": None}}]},
                    },
                ],
                ["Q1", "Q2"],
                {"tool1": 1, "no_tool": 1},
            ),
        ],
    )
    def test_generate_train_embeddings(
        self, mock_es_client, mock_embedding_client, records, queries, expected_binned_data
    ):
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")

        embeddings = [[0.1] * 1024 for q in queries if q]
        mock_embedding_client.get_embeddings_batch.return_value = embeddings

        result = icl_selection._generate_train_embeddings(records)

        for tool_name, expected_count in expected_binned_data.items():
            assert tool_name in result
            assert len(result[tool_name]) == expected_count
            if expected_count > 0:
                embedding, record = result[tool_name][0]
                assert isinstance(embedding, list)
                assert "request" in record

    def test_apply_relevance_coverage_selection(self, icl_selection_factory):
        icl_selection = icl_selection_factory("semantic")
        candidates = [
            (0.9, "tool_a", {"id": 1}),
            (0.8, "tool_a", {"id": 2}),
            (0.7, "tool_b", {"id": 3}),
        ]
        icl_selection.config.similarity_config.relevance_ratio = 0.5
        with patch.object(icl_selection, "format_example", return_value=("Example", 50)):
            result = icl_selection._apply_relevance_coverage_selection(candidates, 3)
            # 1 for relevance, 2 for coverage
            assert len(result["tool_a"]) >= 1
            assert len(result["tool_b"]) >= 1

    def test_end_to_end_embedding_similarity(
        self, mock_es_client, mock_embedding_client, sample_records
    ):
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            max_examples=1,
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        eval_records = sample_records[:1]

        with (
            patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index,
            patch("src.lib.flywheel.icl_selection.search_similar_embeddings") as mock_search,
        ):
            mock_embedding_client.get_embeddings_batch.side_effect = [
                [[0.1] * 1024] * len(sample_records),
                [[0.2] * 1024] * len(eval_records),
            ]
            mock_index.return_value = "test_index"
            mock_search.return_value = [(0.9, "no_tool", sample_records[0])]

            index_name, result = icl_selection._embedding_similarity_selection(
                sample_records, eval_records, WorkloadClassification.GENERIC
            )

            assert index_name == "test_index"
            assert len(result) == 1
            system_message = result[0]["request"]["messages"][0]
            assert system_message["role"] == "system"
            assert "For example" in system_message["content"]
            mock_search.assert_called_once()

    def test_apply_relevance_coverage_all_tools_covered(self, icl_selection_factory):
        """Test relevance-coverage selection when all tools are already covered."""
        icl_selection = icl_selection_factory("semantic")
        # All candidates from tool_a
        candidates = [
            (
                0.9,
                "tool_a",
                {"id": 1, "request": {"messages": []}, "response": {"choices": [{"message": {}}]}},
            ),
            (
                0.8,
                "tool_a",
                {"id": 2, "request": {"messages": []}, "response": {"choices": [{"message": {}}]}},
            ),
            (
                0.7,
                "tool_a",
                {"id": 3, "request": {"messages": []}, "response": {"choices": [{"message": {}}]}},
            ),
        ]
        icl_selection.config.similarity_config.relevance_ratio = 0.33
        with patch.object(icl_selection, "format_example", return_value=("Example", 50)):
            result = icl_selection._apply_relevance_coverage_selection(candidates, 3)
            # Line 234-235 coverage: All tools covered, take next best candidates
            assert "tool_a" in result
            assert len(result["tool_a"]) == 3

    def test_apply_relevance_coverage_empty_candidates(self, icl_selection_factory):
        """Test relevance-coverage selection with empty candidates."""
        icl_selection = icl_selection_factory("semantic")
        # Line 214 coverage: empty candidates
        result = icl_selection._apply_relevance_coverage_selection([], 3)
        assert result == {}

    def test_uniform_binning_dpo_workload(self, icl_selection_factory):
        """Test uniform binning selection with DPO workload type."""
        icl_selection = icl_selection_factory("uniform")
        dpo_records = [
            {
                "prompt": [{"role": "user", "content": f"Question {i}"}],
                "chosen_response": f"Good answer {i}",
                "rejected_response": f"Bad answer {i}",
            }
            for i in range(3)
        ]
        # Line 293 coverage: DPO workload formatting
        result = icl_selection.uniform_binning_selection(
            dpo_records, icl_selection.config, WorkloadClassification.DPO
        )
        assert "generic_examples" in result
        assert len(result["generic_examples"]) > 0

    def test_format_example_empty_string_skip(self, icl_selection_factory):
        """Test that empty formatted examples are skipped in binning."""
        icl_selection = icl_selection_factory("uniform")
        records = [{"request": {"messages": []}, "response": {"choices": [{"message": {}}]}}]
        # Line 296-297 coverage: empty example_str skip
        with patch.object(icl_selection, "format_example", return_value=("", 0)):
            result = icl_selection.uniform_binning_selection(
                records, icl_selection.config, WorkloadClassification.GENERIC
            )
            assert result == {}

    def test_inject_icl_examples_empty_record(self, icl_selection_factory):
        """Test injecting ICL examples with empty record."""
        icl_selection = icl_selection_factory("uniform")
        # Line 343 coverage: empty record
        result, cnts = icl_selection._inject_icl_examples(None, {"tool": [({}, "ex", 10)]})
        assert result is None
        assert cnts == (0, 0, 0)

    def test_inject_icl_examples_no_available_tokens(self, icl_selection_factory):
        """Test injecting ICL examples when no tokens available."""
        icl_selection = icl_selection_factory("uniform")
        icl_selection.config.max_context_length = 100
        icl_selection.config.reserved_tokens = 50
        large_record = {"request": {"messages": [{"role": "user", "content": "x" * 10000}]}}
        # Line 350 coverage: no available tokens
        with patch.object(icl_selection, "estimate_tokens", return_value=100):
            result, cnts = icl_selection._inject_icl_examples(
                large_record, {"tool": [({}, "ex", 10)]}
            )
            assert cnts == (0, 0, 0)

    def test_inject_system_message_no_messages_field(self, icl_selection_factory):
        """Test injecting system message when messages field doesn't exist."""
        icl_selection = icl_selection_factory("uniform")
        record = {"request": {}}
        # Line 374 coverage: no messages field
        icl_selection._inject_system_message(record, "Example content")
        assert "messages" in record["request"]
        assert record["request"]["messages"][0]["role"] == "system"

    def test_inject_dpo_icl_examples_basic(self, icl_selection_factory):
        """Test injecting DPO ICL examples."""
        icl_selection = icl_selection_factory("uniform")
        eval_record = {
            "messages": [{"role": "user", "content": "Test question"}],
            "chosen_response": "Good answer",
            "rejected_response": "Bad answer",
        }
        icl_examples = {
            "no_tool": [
                (
                    {"prompt": [{"role": "user", "content": "Example"}]},
                    "Example 1:\nPrompt: Example\n✓ PREFERRED Response: A\n✗ LESS PREFERRED Response: B\n",
                    50,
                )
            ]
        }
        with patch.object(icl_selection, "estimate_tokens", return_value=100):
            # Lines 409-449 coverage: DPO injection
            result, cnts = icl_selection._inject_dpo_icl_examples(eval_record, icl_examples)
            assert "messages" in result
            assert result["messages"][0]["role"] == "system"
            assert "preferred vs less preferred" in result["messages"][0]["content"].lower()
            assert "chosen_response" in result
            assert "rejected_response" in result

    def test_inject_dpo_no_available_tokens(self, icl_selection_factory):
        """Test DPO injection when no tokens available."""
        icl_selection = icl_selection_factory("uniform")
        eval_record = {
            "messages": [{"role": "user", "content": "Test"}],
            "chosen_response": "A",
            "rejected_response": "B",
        }
        icl_selection.config.max_context_length = 50
        icl_selection.config.reserved_tokens = 40
        with patch.object(icl_selection, "estimate_tokens", return_value=100):
            # Line 417 coverage: no tokens for DPO
            result, cnts = icl_selection._inject_dpo_icl_examples(eval_record, {})
            assert cnts == (0, 0, 0)

    def test_log_injection_stats_no_examples(self, icl_selection_factory, caplog):
        """Test logging when no examples were injected."""
        icl_selection = icl_selection_factory("uniform")
        # Line 470 coverage: no examples injected
        icl_selection._log_injection_stats([{}], [])
        assert "No examples were injected" in caplog.text

    def test_calculate_tool_limits_tool_calling(self, icl_selection_factory):
        """Test calculating tool limits for tool calling workload."""
        icl_selection = icl_selection_factory("uniform")
        icl_selection.config.max_examples = 10
        binned_data = {
            "tool_a": [(None, None)],
            "tool_b": [(None, None)],
            "tool_c": [(None, None)],
        }
        # Lines 479-483 coverage: tool calling limits
        result = icl_selection._calculate_tool_limits(
            WorkloadClassification.TOOL_CALLING, binned_data
        )
        assert len(result) == 3
        assert sum(result.values()) == 10

    def test_calculate_tool_limits_empty_tools(self, icl_selection_factory):
        """Test calculating tool limits with empty tools."""
        icl_selection = icl_selection_factory("uniform")
        # Line 485 coverage: empty tool limits
        result = icl_selection._calculate_tool_limits(WorkloadClassification.TOOL_CALLING, {})
        assert result == {}

    def test_calculate_tool_limits_generic(self, icl_selection_factory):
        """Test calculating tool limits for generic workload."""
        icl_selection = icl_selection_factory("uniform")
        icl_selection.config.max_examples = 5
        binned_data = {"no_tool": [(None, None)]}
        # Line 487-488 coverage: generic limits
        result = icl_selection._calculate_tool_limits(WorkloadClassification.GENERIC, binned_data)
        assert result == {"no_tool": 5}

    def test_generate_train_embeddings_dpo(self, mock_es_client, mock_embedding_client):
        """Test generating embeddings for DPO records."""
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        dpo_records = [
            {
                "prompt": [{"role": "user", "content": "Question 1"}],
                "chosen_response": "Good",
                "rejected_response": "Bad",
            }
        ]
        mock_embedding_client.get_embeddings_batch.return_value = [[0.1] * 1024]
        # Line 517 coverage: DPO embeddings
        result = icl_selection._generate_train_embeddings(dpo_records, WorkloadClassification.DPO)
        assert "no_tool" in result

    def test_embedding_similarity_dpo_extraction(self, mock_es_client, mock_embedding_client):
        """Test embedding similarity selection with DPO extraction."""
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            max_examples=1,
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        dpo_records = [
            {
                "messages": [{"role": "user", "content": "Question"}],
                "chosen_response": "Good",
                "rejected_response": "Bad",
            }
        ]
        with (
            patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index,
            patch("src.lib.flywheel.icl_selection.search_similar_embeddings") as mock_search,
        ):
            mock_embedding_client.get_embeddings_batch.return_value = [[0.1] * 1024]
            mock_index.return_value = "test_index"
            mock_search.return_value = []
            # Lines 575, 587-588 coverage: DPO query extraction in eval
            index_name, result = icl_selection._embedding_similarity_selection(
                dpo_records, dpo_records, WorkloadClassification.DPO
            )
            assert index_name == "test_index"

    def test_embedding_similarity_tool_calling_selection(
        self, mock_es_client, mock_embedding_client, sample_records
    ):
        """Test embedding similarity with tool calling workload."""
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local"),
            relevance_ratio=0.8,
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            max_examples=2,
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        tool_records = [
            {
                **rec,
                "response": {
                    "choices": [{"message": {"tool_calls": [{"function": {"name": "tool_a"}}]}}]
                },
            }
            for rec in sample_records[:2]
        ]
        eval_records = tool_records[:1]

        with (
            patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index,
            patch("src.lib.flywheel.icl_selection.search_similar_embeddings") as mock_search,
        ):
            mock_embedding_client.get_embeddings_batch.side_effect = [
                [[0.1] * 1024] * len(tool_records),
                [[0.2] * 1024] * len(eval_records),
            ]
            mock_index.return_value = "test_index"
            mock_search.return_value = [
                (0.9, "tool_a", tool_records[0]),
                (0.8, "tool_a", tool_records[1]),
            ]
            # Line 605 coverage: tool calling relevance-coverage selection
            index_name, result = icl_selection._embedding_similarity_selection(
                tool_records, eval_records, WorkloadClassification.TOOL_CALLING
            )
            assert index_name == "test_index"

    def test_embedding_similarity_dpo_format(self, mock_es_client, mock_embedding_client):
        """Test embedding similarity with DPO format examples."""
        similarity_config = SimilarityConfig(
            embedding_nim_config=EmbeddingConfig(deployment_type="local")
        )
        config = ICLConfig(
            example_selection="semantic_similarity",
            max_examples=1,
            similarity_config=similarity_config,
        )
        icl_selection = ICLSelection(config, "workload", "client")
        dpo_train_records = [
            {
                "prompt": [{"role": "user", "content": "Train question"}],
                "chosen_response": "Good",
                "rejected_response": "Bad",
            }
        ]
        dpo_eval_records = [
            {
                "messages": [{"role": "user", "content": "Eval question"}],
                "chosen_response": "Good eval",
                "rejected_response": "Bad eval",
            }
        ]
        with (
            patch("src.lib.flywheel.icl_selection.index_embeddings_to_es") as mock_index,
            patch("src.lib.flywheel.icl_selection.search_similar_embeddings") as mock_search,
        ):
            mock_embedding_client.get_embeddings_batch.return_value = [[0.1] * 1024]
            mock_index.return_value = "test_index"
            mock_search.return_value = [(0.9, "no_tool", dpo_train_records[0])]
            # Lines 613, 623 coverage: DPO formatting in generic workload
            index_name, result = icl_selection._embedding_similarity_selection(
                dpo_train_records, dpo_eval_records, WorkloadClassification.DPO
            )
            assert index_name == "test_index"

    def test_generate_icl_records_dpo_uniform(self, icl_selection_factory):
        """Test generating ICL records with DPO workload using uniform selection."""
        icl_selection = icl_selection_factory("uniform")
        icl_selection.config.max_examples = 1
        dpo_source = [
            {
                "prompt": [{"role": "user", "content": "Source question"}],
                "chosen_response": "Good",
                "rejected_response": "Bad",
            }
        ]
        dpo_eval = [
            {
                "messages": [{"role": "user", "content": "Eval question"}],
                "chosen_response": "Correct",
                "rejected_response": "Wrong",
            }
        ]
        # Line 659 coverage: DPO with uniform selection
        index_name, result = icl_selection.generate_icl_records(
            dpo_source, WorkloadClassification.DPO, eval_records=dpo_eval
        )
        assert index_name is None
        assert len(result) == 1
