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
"""Tests for lib.knowledge.adapters.frag_api.

Covers:
- the dict→Milvus filter_expr translator,
- the rag-server hit→Chunk normaliser (filename cleanup, page-number
  handling, content-type dispatch, citation formatting),
- the retrieve() flow: payload shape, dict-vs-callable filter handling,
  error→failure-result mapping.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
import pytest

from lib.knowledge.adapters.frag_api import FragApiAdapter
from lib.knowledge.adapters.frag_api import FragApiConfig
from lib.knowledge.adapters.frag_api import _filters_to_expr
from lib.knowledge.adapters.frag_api import _normalise_search_result
from lib.knowledge.schema import ContentType


class TestFiltersToExpr:
    """Translation of dict filters to Milvus filter_expr strings."""

    def test_filter_expr_passes_through_unchanged(self):
        out = _filters_to_expr({"filter_expr": 'category == "safety"'})
        assert out == 'category == "safety"'

    def test_dict_string_field(self):
        assert _filters_to_expr({"category": "safety"}) == 'category == "safety"'

    def test_dict_numeric_field_unquoted(self):
        # Numeric values must NOT be quoted — Milvus would reject the wrong type.
        assert _filters_to_expr({"page_number": 5}) == "page_number == 5"

    def test_multiple_fields_joined_with_and(self):
        out = _filters_to_expr({"category": "safety", "page_number": 5})
        assert 'category == "safety"' in out
        assert "page_number == 5" in out
        assert " and " in out

    def test_callable_filter_returns_none(self):
        # Predicates run client-side; nothing is pushed to the server.
        assert _filters_to_expr(lambda _chunk: True) is None

    def test_empty_dict_returns_none(self):
        assert _filters_to_expr({}) is None


class TestNormaliseSearchResult:
    """rag-server /search hit → Chunk normalisation."""

    def test_strips_tmp_prefix_from_filename(self):
        # Ingestion-time tmp prefix shape: `tmp` + 8 chars + `_`.
        chunk = _normalise_search_result({"document_name": "tmpABCDEF12_Forklift.pdf", "content": "x"})
        assert chunk.metadata["file_name"] == "Forklift.pdf"

    @pytest.mark.parametrize("bad_page", [-1, 0, None])
    def test_invalid_page_numbers_become_none(self, bad_page):
        chunk = _normalise_search_result({"document_name": "F.pdf", "content": "x", "page_number": bad_page})
        assert chunk.metadata["page_number"] is None
        assert chunk.metadata["display_citation"] == "[F.pdf]"

    def test_valid_page_number_in_citation(self):
        chunk = _normalise_search_result({"document_name": "Manual.pdf", "content": "x", "page_number": 3})
        assert chunk.metadata["page_number"] == 3
        assert chunk.metadata["display_citation"] == "[Manual.pdf, p.3]"

    @pytest.mark.parametrize(
        "doc_type,expected",
        [
            ("image", ContentType.IMAGE),
            ("table", ContentType.TABLE),
            ("chart", ContentType.CHART),
            ("text", ContentType.TEXT),
            ("anything_else", ContentType.TEXT),
        ],
    )
    def test_content_type_dispatch(self, doc_type, expected):
        chunk = _normalise_search_result({"content": "x", "document_type": doc_type})
        assert chunk.metadata["content_type"] is expected

    def test_non_dict_input_returns_none(self):
        # Defensive against unexpected payload shapes from upstream.
        assert _normalise_search_result("not a dict") is None


class TestFragApiAdapter:
    """Behaviour of the HTTP adapter (mocked transport)."""

    @pytest.fixture
    def adapter(self):
        return FragApiAdapter(FragApiConfig(rag_url="http://rag-server:8081/v1", timeout=30, verify_ssl=True))

    @staticmethod
    def _mock_response(json_body=None, raise_for_status_exc=None):
        """An aiohttp response that's its own async context manager."""
        resp = AsyncMock()
        resp.json = AsyncMock(return_value=json_body or {})
        resp.raise_for_status = MagicMock(side_effect=raise_for_status_exc)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        return resp

    @staticmethod
    def _mock_session(post_return=None, post_side_effect=None):
        """An aiohttp.ClientSession that's its own async context manager."""
        session = AsyncMock()
        session.post = MagicMock(return_value=post_return, side_effect=post_side_effect)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    def test_authorization_header_only_when_api_key_set(self):
        without = FragApiAdapter(FragApiConfig(rag_url="http://x/v1"))
        with_key = FragApiAdapter(
            FragApiConfig(
                rag_url="http://x/v1",
                api_key="test-token",  # pragma: allowlist secret
            )
        )
        assert "Authorization" not in without._headers()
        assert with_key._headers()["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_retrieve_posts_to_search_endpoint_with_expected_payload(self, adapter):
        resp = self._mock_response(
            json_body={
                "results": [
                    {"document_name": "Manual.pdf", "content": "first", "score": 0.9, "page_number": 1},
                ]
            }
        )
        session = self._mock_session(post_return=resp)

        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="hello", collection_name="warehouse", top_k=5)

        assert session.post.call_args.args[0] == "http://rag-server:8081/v1/search"
        payload = session.post.call_args.kwargs["json"]
        assert payload["query"] == "hello"
        assert payload["collection_names"] == ["warehouse"]
        assert payload["reranker_top_k"] == 5
        assert result.success is True
        assert len(result.chunks) == 1
        assert result.chunks[0].content == "first"

    @pytest.mark.asyncio
    async def test_dict_filter_pushed_down_as_filter_expr(self, adapter):
        resp = self._mock_response(json_body={"results": []})
        session = self._mock_session(post_return=resp)

        with patch("aiohttp.ClientSession", return_value=session):
            await adapter.retrieve(
                query="x",
                collection_name="c",
                filters={"filter_expr": 'content_metadata["filename"] == "F.pdf"'},
            )

        assert session.post.call_args.kwargs["json"]["filter_expr"] == 'content_metadata["filename"] == "F.pdf"'

    @pytest.mark.asyncio
    async def test_callable_filter_applied_client_side_not_pushed(self, adapter):
        resp = self._mock_response(
            json_body={
                "results": [
                    {"document_name": "A.pdf", "content": "keep", "score": 0.9},
                    {"document_name": "B.pdf", "content": "drop", "score": 0.8},
                ]
            }
        )
        session = self._mock_session(post_return=resp)

        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(
                query="x",
                collection_name="c",
                filters=lambda chunk: chunk.metadata.get("file_name") == "A.pdf",
            )

        # Predicate filters run client-side; nothing pushed to the server.
        assert "filter_expr" not in session.post.call_args.kwargs["json"]
        assert [c.metadata["file_name"] for c in result.chunks] == ["A.pdf"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc,expected_substring",
        [
            (aiohttp.ClientConnectionError("refused"), "Cannot connect"),
            (TimeoutError("slow"), "timed out"),
            (aiohttp.ClientError("misc"), "Request failed"),
        ],
    )
    async def test_transport_errors_map_to_failure_result(self, adapter, exc, expected_substring):
        session = self._mock_session(post_side_effect=exc)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="x", collection_name="c")
        assert result.success is False
        assert expected_substring in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_http_error_maps_to_failure_result(self, adapter):
        resp = self._mock_response(
            raise_for_status_exc=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=500, message="Server Error"
            )
        )
        session = self._mock_session(post_return=resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="x", collection_name="c")
        assert result.success is False
        assert "Server error" in (result.error_message or "")
