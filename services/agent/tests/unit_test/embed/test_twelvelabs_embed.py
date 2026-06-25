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
"""Unit tests for twelvelabs_embed module.

The no-network tests patch the TwelveLabs SDK so they run without the optional
``twelvelabs`` dependency installed. The ``TestLive*`` cases hit the real API
and are skipped unless ``TWELVELABS_API_KEY`` is set.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

from vss_agents.embed.twelvelabs_embed import TWELVELABS_EMBED_DIM
from vss_agents.embed.twelvelabs_embed import TwelveLabsEmbedClient


def _make_client_with_mock() -> tuple[TwelveLabsEmbedClient, MagicMock]:
    """Build a client whose SDK ``TwelveLabs`` is a mock, no network, no install."""
    fake_sdk = MagicMock()
    fake_instance = MagicMock()
    fake_sdk.return_value = fake_instance
    fake_module = types.ModuleType("twelvelabs")
    fake_module.TwelveLabs = fake_sdk  # type: ignore[attr-defined]
    saved = sys.modules.get("twelvelabs")
    sys.modules["twelvelabs"] = fake_module
    try:
        client = TwelveLabsEmbedClient(api_key="test-key")
    finally:
        if saved is not None:
            sys.modules["twelvelabs"] = saved
        else:
            sys.modules.pop("twelvelabs", None)
    return client, fake_instance


def _segment(values: list[float]) -> MagicMock:
    seg = MagicMock()
    seg.float_ = values
    return seg


class TestInit:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("TWELVELABS_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            TwelveLabsEmbedClient()

    def test_default_model(self):
        client, _ = _make_client_with_mock()
        assert client.model_name == "marengo3.0"

    def test_custom_model(self):
        fake_module = types.ModuleType("twelvelabs")
        fake_module.TwelveLabs = MagicMock()  # type: ignore[attr-defined]
        sys.modules["twelvelabs"] = fake_module
        try:
            client = TwelveLabsEmbedClient(api_key="k", model_name="marengo-custom")
            assert client.model_name == "marengo-custom"
        finally:
            sys.modules.pop("twelvelabs", None)

    def test_embed_dim_constant(self):
        assert TWELVELABS_EMBED_DIM == 512


class TestGetTextEmbedding:
    @pytest.mark.asyncio
    async def test_success(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.create.return_value = MagicMock(text_embedding=MagicMock(segments=[_segment([0.1, 0.2, 0.3])]))

        result = await client.get_text_embedding("hello world")

        assert result == [0.1, 0.2, 0.3]
        _, kwargs = sdk.embed.create.call_args
        assert kwargs["text"] == "hello world"
        assert kwargs["model_name"] == "marengo3.0"

    @pytest.mark.asyncio
    async def test_caches_repeat_queries(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.create.return_value = MagicMock(text_embedding=MagicMock(segments=[_segment([1.0])]))

        await client.get_text_embedding("same")
        await client.get_text_embedding("same")

        # Second call served from cache -> only one network round-trip.
        assert sdk.embed.create.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_segments_raises(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.create.return_value = MagicMock(text_embedding=MagicMock(segments=[]))

        with pytest.raises(ValueError, match="no text embedding segments"):
            await client.get_text_embedding("x")


class TestGetImageEmbedding:
    @pytest.mark.asyncio
    async def test_success(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.create.return_value = MagicMock(image_embedding=MagicMock(segments=[_segment([0.4, 0.5])]))

        result = await client.get_image_embedding("http://example.com/cat.jpg")

        assert result == [0.4, 0.5]
        _, kwargs = sdk.embed.create.call_args
        assert kwargs["image_url"] == "http://example.com/cat.jpg"


class TestGetVideoEmbedding:
    @pytest.mark.asyncio
    async def test_success(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.tasks.create.return_value = MagicMock(id="task-123")
        sdk.embed.tasks.status.return_value = MagicMock(status="ready")
        sdk.embed.tasks.retrieve.return_value = MagicMock(video_embedding=MagicMock(segments=[_segment([0.7, 0.8])]))

        result = await client.get_video_embedding("http://example.com/clip.mp4")

        assert result == [0.7, 0.8]
        sdk.embed.tasks.status.assert_called_with(task_id="task-123")
        sdk.embed.tasks.retrieve.assert_called_once_with(task_id="task-123")
        _, kwargs = sdk.embed.tasks.create.call_args
        assert kwargs["video_url"] == "http://example.com/clip.mp4"
        assert kwargs["video_embedding_scope"] == ["video"]

    @pytest.mark.asyncio
    async def test_task_failure_raises(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.tasks.create.return_value = MagicMock(id="task-x")
        sdk.embed.tasks.status.return_value = MagicMock(status="failed")

        with pytest.raises(ValueError, match="failed"):
            await client.get_video_embedding("http://example.com/clip.mp4")

    @pytest.mark.asyncio
    async def test_no_segments_raises(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.tasks.create.return_value = MagicMock(id="t")
        sdk.embed.tasks.status.return_value = MagicMock(status="ready")
        sdk.embed.tasks.retrieve.return_value = MagicMock(video_embedding=MagicMock(segments=[]))

        with pytest.raises(ValueError, match="no video embedding segments"):
            await client.get_video_embedding("http://example.com/clip.mp4")


class TestAclose:
    @pytest.mark.asyncio
    async def test_clears_cache(self):
        client, sdk = _make_client_with_mock()
        sdk.embed.create.return_value = MagicMock(text_embedding=MagicMock(segments=[_segment([1.0])]))
        await client.get_text_embedding("cached")
        assert len(client._text_cache) == 1

        await client.aclose()
        assert len(client._text_cache) == 0


@pytest.mark.skipif(
    not os.getenv("TWELVELABS_API_KEY"),
    reason="TWELVELABS_API_KEY not set; skipping live TwelveLabs API test",
)
class TestLive:
    """Live smoke tests against the real TwelveLabs API (requires the SDK + key)."""

    @pytest.mark.asyncio
    async def test_text_embedding_dim(self):
        pytest.importorskip("twelvelabs")
        client = TwelveLabsEmbedClient()
        vec = await client.get_text_embedding("a person walking a dog")
        assert len(vec) == TWELVELABS_EMBED_DIM
        assert all(isinstance(x, float) for x in vec)
        await client.aclose()
