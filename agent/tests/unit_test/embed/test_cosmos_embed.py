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
"""Unit tests for cosmos_embed module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import httpx
import pytest
from vss_agents.embed.cosmos_embed import CosmosEmbedClient


class TestCosmosEmbedClient:
    """Test CosmosEmbedClient class."""

    def test_init(self):
        client = CosmosEmbedClient("http://localhost:8080")
        assert client.endpoint == "http://localhost:8080"
        assert client.text_embeddings_url == "http://localhost:8080/v1/generate_text_embeddings"
        assert client.image_embeddings_url == "http://localhost:8080/v1/generate_image_embeddings"
        assert client.video_embeddings_url == "http://localhost:8080/v1/generate_video_embeddings"

    def test_init_with_trailing_slash(self):
        # Test that URLs are constructed correctly even with trailing slash
        client = CosmosEmbedClient("http://localhost:8080/")
        # Note: the current implementation doesn't strip trailing slash
        assert client.endpoint == "http://localhost:8080/"

    def test_init_lazy_client(self):
        # Client is lazily initialized — not created in __init__
        client = CosmosEmbedClient("http://localhost:8080")
        assert client._client is None

    def test_init_has_cache(self):
        client = CosmosEmbedClient("http://localhost:8080")
        assert len(client._text_cache) == 0


def _make_client_with_mock() -> tuple[CosmosEmbedClient, AsyncMock]:
    """Create a CosmosEmbedClient with a mocked httpx client for testing."""
    client = CosmosEmbedClient("http://localhost:8080")
    mock_http = AsyncMock()
    client._client = mock_http
    return client, mock_http


class TestGetImageEmbedding:
    """Test get_image_embedding method."""

    @pytest.mark.asyncio
    async def test_get_image_embedding_base64(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        mock_http.post.return_value = mock_response

        result = await client.get_image_embedding("data:image/jpeg;base64,abc123")

        assert result == [0.1, 0.2, 0.3]
        mock_http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_image_embedding_url(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.4, 0.5, 0.6]}]}
        mock_http.post.return_value = mock_response

        result = await client.get_image_embedding("http://example.com/image.jpg")

        assert result == [0.4, 0.5, 0.6]
        # Check that presigned_url format was used
        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert "presigned_url" in payload["input"][0]

    @pytest.mark.asyncio
    async def test_get_image_embedding_http_error(self):
        client, mock_http = _make_client_with_mock()
        mock_http.post.side_effect = httpx.HTTPError("Connection failed")

        with pytest.raises(httpx.HTTPError):
            await client.get_image_embedding("http://example.com/image.jpg")


class TestGetTextEmbedding:
    """Test get_text_embedding method."""

    @pytest.mark.asyncio
    async def test_get_text_embedding_success(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embeddings": [0.7, 0.8, 0.9]}]}
        mock_http.post.return_value = mock_response

        result = await client.get_text_embedding("hello world")

        assert result == [0.7, 0.8, 0.9]
        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["text_input"] == ["hello world"]

    @pytest.mark.asyncio
    async def test_get_text_embedding_http_error(self):
        client, mock_http = _make_client_with_mock()
        mock_http.post.side_effect = httpx.HTTPError("Connection failed")

        with pytest.raises(httpx.HTTPError):
            await client.get_text_embedding("test text")

    @pytest.mark.asyncio
    async def test_get_text_embedding_cache_hit(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embeddings": [0.1, 0.2]}]}
        mock_http.post.return_value = mock_response

        # First call — cache miss
        result1 = await client.get_text_embedding("hello")
        assert result1 == [0.1, 0.2]
        assert mock_http.post.call_count == 1

        # Second call — cache hit, no additional HTTP call
        result2 = await client.get_text_embedding("hello")
        assert result2 == [0.1, 0.2]
        assert mock_http.post.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_get_text_embedding_different_keys(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embeddings": [0.1, 0.2]}]}
        mock_http.post.return_value = mock_response

        await client.get_text_embedding("hello")
        await client.get_text_embedding("world")
        assert mock_http.post.call_count == 2  # different keys, both fetched


class TestGetVideoEmbedding:
    """Test get_video_embedding method."""

    @pytest.mark.asyncio
    async def test_get_video_embedding_success(self):
        client = CosmosEmbedClient("http://localhost:8080")
        mock_get = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        client.get_video_embeddings_from_urls = mock_get

        result = await client.get_video_embedding("http://example.com/video.mp4")

        assert result == [0.1, 0.2, 0.3]
        mock_get.assert_called_once_with(["http://example.com/video.mp4"])


class TestGetVideoEmbeddingsFromUrls:
    """Test get_video_embeddings_from_urls method."""

    @pytest.mark.asyncio
    async def test_get_video_embeddings_single_url(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        mock_http.post.return_value = mock_response

        result = await client.get_video_embeddings_from_urls(["http://example.com/video.mp4"])

        assert result == [[0.1, 0.2, 0.3]]
        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert "presigned_url" in payload["input"][0]
        assert payload["request_type"] == "bulk_video"

    @pytest.mark.asyncio
    async def test_get_video_embeddings_multiple_urls(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }
        mock_http.post.return_value = mock_response

        result = await client.get_video_embeddings_from_urls(
            [
                "http://example.com/video1.mp4",
                "http://example.com/video2.mp4",
            ]
        )

        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_get_video_embeddings_url_formatting(self):
        client, mock_http = _make_client_with_mock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1]}]}
        mock_http.post.return_value = mock_response

        await client.get_video_embeddings_from_urls(["http://test.com/video.mp4"])

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        # Check URL formatting
        assert payload["input"][0] == "data:video/mp4;presigned_url,http://test.com/video.mp4"
        assert payload["model"] == "cosmos-embed1-448p"
        assert payload["encoding_format"] == "float"


class TestLRUCache:
    """Test bounded LRU cache behavior on CosmosEmbedClient."""

    @pytest.mark.asyncio
    async def test_lru_cache_eviction_evicts_oldest(self):
        """Filling the cache past maxsize evicts the LRU entry and its lock."""
        client, mock_http = _make_client_with_mock()
        # Shrink cache for the test
        from vss_agents.embed.embed import LRUEmbeddingCache

        client._text_cache = LRUEmbeddingCache(maxsize=2)

        def _make_response(vec):
            resp = MagicMock()
            resp.json.return_value = {"data": [{"embeddings": vec}]}
            return resp

        mock_http.post.side_effect = [
            _make_response([1.0]),
            _make_response([2.0]),
            _make_response([3.0]),
        ]

        await client.get_text_embedding("a")
        await client.get_text_embedding("b")
        await client.get_text_embedding("c")  # evicts "a"

        # "a" evicted, "b" and "c" present
        assert client._text_cache.get("a") is None
        assert client._text_cache.get("b") == [2.0]
        assert client._text_cache.get("c") == [3.0]
        # matching lock for "a" also evicted
        assert "a" not in client._text_cache._locks

    @pytest.mark.asyncio
    async def test_lru_cache_touch_on_access(self):
        """Accessing an entry marks it as recently used, avoiding eviction."""
        client, mock_http = _make_client_with_mock()
        from vss_agents.embed.embed import LRUEmbeddingCache

        client._text_cache = LRUEmbeddingCache(maxsize=2)

        def _make_response(vec):
            resp = MagicMock()
            resp.json.return_value = {"data": [{"embeddings": vec}]}
            return resp

        mock_http.post.side_effect = [
            _make_response([1.0]),
            _make_response([2.0]),
            _make_response([3.0]),
        ]

        await client.get_text_embedding("a")
        await client.get_text_embedding("b")
        # Touch "a" so it becomes MRU
        await client.get_text_embedding("a")
        await client.get_text_embedding("c")  # should evict "b", not "a"

        assert client._text_cache.get("a") == [1.0]
        assert client._text_cache.get("b") is None
        assert client._text_cache.get("c") == [3.0]


class TestAClose:
    """Test resource cleanup via aclose()."""

    @pytest.mark.asyncio
    async def test_aclose_closes_client_and_clears_cache(self):
        """aclose() closes the http client (sets to None) and clears the cache."""
        client, mock_http = _make_client_with_mock()
        mock_http.aclose = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embeddings": [0.1]}]}
        mock_http.post.return_value = mock_response

        # Populate cache
        await client.get_text_embedding("hello")
        assert len(client._text_cache) == 1

        await client.aclose()

        mock_http.aclose.assert_called_once()
        assert client._client is None
        assert len(client._text_cache) == 0

    @pytest.mark.asyncio
    async def test_aclose_no_client_noop(self):
        """aclose() is safe when no client was ever created."""
        client = CosmosEmbedClient("http://localhost:8080")
        assert client._client is None
        await client.aclose()  # must not raise
        assert client._client is None
