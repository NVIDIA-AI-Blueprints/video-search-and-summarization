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
from unittest.mock import patch

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


class TestGetImageEmbedding:
    """Test get_image_embedding method."""

    @pytest.fixture
    def client(self):
        return CosmosEmbedClient("http://localhost:8080")

    @pytest.mark.asyncio
    async def test_get_image_embedding_base64(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
            mock_client.post.return_value = mock_response

            result = await client.get_image_embedding("data:image/jpeg;base64,abc123")

            assert result == [0.1, 0.2, 0.3]
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_image_embedding_url(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embedding": [0.4, 0.5, 0.6]}]}
            mock_client.post.return_value = mock_response

            result = await client.get_image_embedding("http://example.com/image.jpg")

            assert result == [0.4, 0.5, 0.6]
            # Check that presigned_url format was used
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            assert "presigned_url" in payload["input"][0]

    @pytest.mark.asyncio
    async def test_get_image_embedding_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = httpx.HTTPError("Connection failed")

            with pytest.raises(httpx.HTTPError):
                await client.get_image_embedding("http://example.com/image.jpg")


class TestGetTextEmbedding:
    """Test get_text_embedding method."""

    @pytest.fixture
    def client(self):
        return CosmosEmbedClient("http://localhost:8080")

    @pytest.mark.asyncio
    async def test_get_text_embedding_success(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embeddings": [0.7, 0.8, 0.9]}]}
            mock_client.post.return_value = mock_response

            result = await client.get_text_embedding("hello world")

            assert result == [0.7, 0.8, 0.9]
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            assert payload["text_input"] == ["hello world"]

    @pytest.mark.asyncio
    async def test_get_text_embedding_http_error(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = httpx.HTTPError("Connection failed")

            with pytest.raises(httpx.HTTPError):
                await client.get_text_embedding("test text")


class TestGetVideoEmbedding:
    """Test get_video_embedding method."""

    @pytest.fixture
    def client(self):
        return CosmosEmbedClient("http://localhost:8080")

    @pytest.mark.asyncio
    async def test_get_video_embedding_success(self, client):
        with patch.object(client, "get_video_embeddings_from_urls") as mock_get:
            mock_get.return_value = [[0.1, 0.2, 0.3]]

            result = await client.get_video_embedding("http://example.com/video.mp4")

            assert result == [0.1, 0.2, 0.3]
            mock_get.assert_called_once_with(["http://example.com/video.mp4"])


class TestGetVideoEmbeddingsFromUrls:
    """Test get_video_embeddings_from_urls method."""

    @pytest.fixture
    def client(self):
        return CosmosEmbedClient("http://localhost:8080")

    @pytest.mark.asyncio
    async def test_get_video_embeddings_single_url(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
            mock_client.post.return_value = mock_response

            result = await client.get_video_embeddings_from_urls(["http://example.com/video.mp4"])

            assert result == [[0.1, 0.2, 0.3]]
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            assert "presigned_url" in payload["input"][0]
            assert payload["request_type"] == "bulk_video"

    @pytest.mark.asyncio
    async def test_get_video_embeddings_multiple_urls(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            }
            mock_client.post.return_value = mock_response

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
    async def test_get_video_embeddings_url_formatting(self, client):
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embedding": [0.1]}]}
            mock_client.post.return_value = mock_response

            await client.get_video_embeddings_from_urls(["http://test.com/video.mp4"])

            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            # Check URL formatting
            assert payload["input"][0] == "data:video/mp4;presigned_url,http://test.com/video.mp4"
            assert payload["model"] == "cosmos-embed1-448p"
            assert payload["encoding_format"] == "float"
