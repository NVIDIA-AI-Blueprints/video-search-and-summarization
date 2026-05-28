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

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from lib.knowledge.schema import Chunk
from lib.knowledge.schema import ContentType
from lib.knowledge.schema import RetrievalResult
from vss_agents.register_knowledge_layers import KnowledgeRetrievalConfig
from vss_agents.register_knowledge_layers import KnowledgeRetrievalInput
from vss_agents.register_knowledge_layers import _format_results
from vss_agents.register_knowledge_layers import _setup_backend
from vss_agents.register_knowledge_layers import knowledge_retrieval
from vss_agents.tools.lvs_media_state import LVSConfiguredMedia


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
        assert cfg.top_k == 5
        assert cfg.backend_config == {}
        assert cfg.generate_summary is False

    def test_unknown_top_level_field_rejected(self):
        # `extra="forbid"` — backend-specific knobs (incl. collection_name)
        # must live under `backend_config`, not at top level.
        with pytest.raises(ValueError):
            KnowledgeRetrievalConfig(collection_name="some_default")
        with pytest.raises(ValueError):
            KnowledgeRetrievalConfig(rag_url="http://rag:8081/v1")


class TestSetupBackend:
    """`_setup_backend` returns the backend config dict for the factory."""

    def test_backend_config_routed_to_adapter(self):
        cfg = KnowledgeRetrievalConfig(
            backend="frag_api",
            backend_config={
                "rag_url": "http://rag:8081/v1",
                "api_key": "test-token",  # pragma: allowlist secret
                "timeout": 120,
                "verify_ssl": False,
            },
        )
        backend, adapter_cfg = _setup_backend(cfg, MagicMock())
        assert backend == "frag_api"
        assert adapter_cfg == {
            "rag_url": "http://rag:8081/v1",
            "api_key": "test-token",  # pragma: allowlist secret
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
                    metadata={
                        "file_name": "Manual.pdf",
                        "page_number": 3,
                        "content_type": ContentType.TEXT,
                    },
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
            chunks=[Chunk(chunk_id="c", content=long_text, score=0.5, metadata={"file_name": "F.pdf"})],
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
            chunks=[Chunk(chunk_id="c", content="x", score=0.5, metadata={"file_name": "F.pdf"})],
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
            top_k=5,
            backend_config={"rag_url": "http://rag:8081/v1"},
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    async def _get_inner_fn(self, config, mock_builder):
        gen = knowledge_retrieval.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_passes_empty_collection_when_input_omits_one(self, config, mock_builder):
        """When tool_input.collection is None, _search passes an empty string;
        the adapter is responsible for substituting its own backend-configured
        default (`FragApiConfig.collection_name`, etc.)."""
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(query="q", backend="frag_api", success=True, chunks=[])
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever",
            new=AsyncMock(return_value=mock_retriever),
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(KnowledgeRetrievalInput(query="hello"))

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["collection_name"] == ""

    @pytest.mark.asyncio
    async def test_explicit_input_collection_overrides_default(self, config, mock_builder):
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(query="q", backend="frag_api", success=True, chunks=[])
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever",
            new=AsyncMock(return_value=mock_retriever),
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(KnowledgeRetrievalInput(query="hello", collection="other_collection"))

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["collection_name"] == "other_collection"

    @pytest.mark.asyncio
    async def test_tool_description_carries_backend_specific_hint(self, config, mock_builder):
        """Each adapter's `tool_description_hint` is appended to the tool description.

        Lets each backend teach the LLM how to call the tool (filter shape,
        defaults) without leaking backend specifics into workflow prompts.
        """
        mock_retriever = AsyncMock()
        mock_retriever.__class__.tool_description_hint = "BACKEND_SPECIFIC_USAGE_HINT"
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever",
            new=AsyncMock(return_value=mock_retriever),
        ):
            gen = knowledge_retrieval.__wrapped__(config, mock_builder)
            function_info = await gen.__anext__()

        assert "BACKEND_SPECIFIC_USAGE_HINT" in function_info.description
        # The base description (backend-agnostic) is still present.
        assert "Returns excerpts with citation tags" in function_info.description

    @pytest.mark.asyncio
    async def test_filters_passed_through_to_retriever(self, config, mock_builder):
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(query="q", backend="frag_api", success=True, chunks=[])
        with patch(
            "vss_agents.register_knowledge_layers.get_retriever",
            new=AsyncMock(return_value=mock_retriever),
        ):
            inner_fn = await self._get_inner_fn(config, mock_builder)
            await inner_fn(
                KnowledgeRetrievalInput(
                    query="hello",
                    filters={"filter_expr": 'content_metadata["filename"] == "F.pdf"'},
                )
            )

        kwargs = mock_retriever.retrieve.call_args.kwargs
        assert kwargs["filters"] == {"filter_expr": 'content_metadata["filename"] == "F.pdf"'}


class TestStreamNameResolution:
    """Cache-hit path: friendly name swapped for the cached `media_id` before
    the adapter sees it. Miss/empty paths are exercised by the existing
    `TestKnowledgeRetrievalInner` tests, which run with an empty cache and
    assert the input passes through verbatim."""

    @pytest.mark.asyncio
    async def test_cache_hit_resolves_friendly_name_to_media_id(self):
        config = KnowledgeRetrievalConfig(
            backend="es_caption", top_k=5, backend_config={"elasticsearch_url": "http://es:9200"}
        )
        mock_retriever = AsyncMock()
        mock_retriever.retrieve.return_value = RetrievalResult(query="q", backend="es_caption", success=True, chunks=[])
        resolved_uuid = "90465930-cab4-4ede-b106-aae5c54ab814"
        cached = LVSConfiguredMedia(
            media_type="stream",
            media_name="warehouse_sample_test",
            media_id=resolved_uuid,
            media_url="rtsp://x",
            scenario="warehouse",
            events=(),
            objects_of_interest=(),
        )
        with (
            patch(
                "vss_agents.register_knowledge_layers.get_retriever",
                new=AsyncMock(return_value=mock_retriever),
            ),
            patch("vss_agents.register_knowledge_layers.configured_media", return_value=cached),
        ):
            gen = knowledge_retrieval.__wrapped__(config, AsyncMock())
            inner_fn = (await gen.__anext__()).single_fn
            await inner_fn(KnowledgeRetrievalInput(query="q", collection="warehouse_sample_test"))

        assert mock_retriever.retrieve.call_args.kwargs["collection_name"] == resolved_uuid
