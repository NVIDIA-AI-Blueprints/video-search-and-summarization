# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for vss_agents.register_knowledge_layers (NAT bridge).

Covers config validation, backend dispatch, result formatting, and the
inner _search path via __wrapped__.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.knowledge.schema import Chunk, ContentType, RetrievalResult
from vss_agents.register_knowledge_layers import (
    KnowledgeRetrievalConfig,
    KnowledgeRetrievalInput,
    _format_results,
    _setup_backend,
    knowledge_retrieval,
)


class TestKnowledgeRetrievalConfig:
    """Pydantic config — only the cross-field validation worth testing."""

    def test_top_k_bounded(self):
        # ge=1, le=50 enforced by Field(...).
        with pytest.raises(ValueError):
            KnowledgeRetrievalConfig(top_k=0)
        with pytest.raises(ValueError):
            KnowledgeRetrievalConfig(top_k=51)

    def test_defaults_match_design(self):
        cfg = KnowledgeRetrievalConfig()
        assert cfg.backend == "frag_api"
        assert cfg.collection_name == "default"
        assert cfg.top_k == 5
        assert cfg.verify_ssl is True
        assert cfg.generate_summary is False


class TestSetupBackend:
    """Translate flat NAT config into the adapter's plain config dict."""

    def test_frag_api_returns_expected_keys(self):
        cfg = KnowledgeRetrievalConfig(
            backend="frag_api",
            rag_url="http://rag:8081/v1",
            api_key="secret",
            timeout=120,
            verify_ssl=False,
        )
        backend, adapter_cfg = _setup_backend(cfg, MagicMock())
        assert backend == "frag_api"
        assert adapter_cfg == {
            "rag_url": "http://rag:8081/v1",
            "api_key": "secret",
            "timeout": 120,
            "verify_ssl": False,
        }


class TestFormatResults:
    """Result-string rendering for the LLM-facing tool output."""

    def test_failure_includes_error_and_query(self):
        result = RetrievalResult(
            query="q",
            backend="frag_api",
            success=False,
            error_message="connection refused",
        )
        out = _format_results(result, query="q")
        assert "Knowledge retrieval failed" in out
        assert "connection refused" in out
        assert "'q'" in out

    def test_failure_without_error_message_uses_unknown(self):
        result = RetrievalResult(query="q", backend="frag_api", success=False)
        out = _format_results(result, query="q")
        assert "unknown error" in out

    def test_empty_success_says_no_results(self):
        result = RetrievalResult(query="q", backend="frag_api", success=True, chunks=[])
        assert "No relevant documents" in _format_results(result, query="q")

    def test_chunks_render_with_citation_and_score(self):
        result = RetrievalResult(
            backend="frag_api",
            success=True,
            chunks=[
                Chunk(
                    chunk_id="c1",
                    content="body text",
                    score=0.87,
                    file_name="Manual.pdf",
                    page_number=3,
                    content_type=ContentType.TEXT,
                ),
            ],
        )
        out = _format_results(result, query="q")
        assert "--- Result 1 ---" in out
        assert "Source: Manual.pdf" in out
        assert "Page: 3" in out
        assert "Manual.pdf, p.3" in out
        assert "0.87" in out
        assert "body text" in out

    def test_long_content_truncated(self):
        long_text = "x" * 2000
        result = RetrievalResult(
            backend="frag_api",
            success=True,
            chunks=[Chunk(chunk_id="c", content=long_text, file_name="F.pdf")],
        )
        out = _format_results(result, query="q")
        assert "[truncated]" in out
        # Output must not contain the full 2000 chars.
        assert "x" * 2000 not in out

    def test_summary_renders_above_results(self):
        result = RetrievalResult(
            backend="frag_api",
            success=True,
            summary="Top-line answer.",
            chunks=[Chunk(chunk_id="c", content="x", file_name="F.pdf")],
        )
        out = _format_results(result, query="q")
        # Summary appears before the per-chunk results.
        assert out.index("Top-line answer.") < out.index("--- Result 1 ---")


class TestKnowledgeRetrievalInner:
    """Inner _search function reached via the NAT generator wrapper."""

    @pytest.fixture
    def config(self):
        return KnowledgeRetrievalConfig(
            backend="frag_api",
            collection_name="vss_warehouse_rules",
            top_k=5,
            rag_url="http://rag:8081/v1",
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    async def _get_inner_fn(self, config, mock_builder):
        gen = knowledge_retrieval.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_uses_config_default_collection_when_input_omits_one(
        self, config, mock_builder
    ):
        """When tool_input.collection is None, the configured default applies."""
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(
            query="q", backend="frag_api", success=True, chunks=[]
        )
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever", return_value=mock_retriever
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(KnowledgeRetrievalInput(query="hello"))

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["collection_name"] == "vss_warehouse_rules"

    @pytest.mark.asyncio
    async def test_explicit_input_collection_overrides_default(self, config, mock_builder):
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(
            query="q", backend="frag_api", success=True, chunks=[]
        )
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever", return_value=mock_retriever
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(
                KnowledgeRetrievalInput(query="hello", collection="other_collection")
            )

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["collection_name"] == "other_collection"

    @pytest.mark.asyncio
    async def test_filters_passed_through_to_retriever(self, config, mock_builder):
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(
            query="q", backend="frag_api", success=True, chunks=[]
        )
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever", return_value=mock_retriever
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(
                KnowledgeRetrievalInput(
                    query="hello",
                    filters={"filter_expr": 'content_metadata["filename"] == "F.pdf"'},
                )
            )

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["filters"] == {
            "filter_expr": 'content_metadata["filename"] == "F.pdf"'
        }
