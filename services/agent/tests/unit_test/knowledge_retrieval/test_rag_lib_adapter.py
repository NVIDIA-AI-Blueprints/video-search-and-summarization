# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for lib.knowledge.adapters.rag_lib.

The `nvidia-rag` package is an optional extra (`vss-agents[rag_lib]`) and
isn't installed in the default test environment. The adapter does a deferred
import inside `__init__`, so we inject fake `nvidia_rag.*` modules into
`sys.modules` BEFORE constructing the adapter — that way the deferred
import resolves to our fakes and we never actually need the package.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fake nvidia_rag injection
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_nvidia_rag(monkeypatch):
    """Install fake `nvidia_rag.*` modules so the adapter's deferred import
    resolves. Returns the (nvidia_rag_cls, nvidia_rag_config_cls, rag_config_instance)
    mocks so individual tests can inspect calls or wire return values."""

    nvidia_rag_cls = MagicMock(name="NvidiaRAG_class")
    nvidia_rag_config_cls = MagicMock(name="NvidiaRAGConfig_class")

    # Each subscriptable section ('llm', 'embeddings', 'vector_store') needs
    # to look like a pydantic model with .model_copy(update=...) — the
    # adapter calls that to merge in user overrides.
    def _make_section() -> MagicMock:
        section = MagicMock()
        section.model_copy = MagicMock(side_effect=lambda **_kwargs: section)
        return section

    rag_config_instance = MagicMock()
    rag_config_instance.llm = _make_section()
    rag_config_instance.embeddings = _make_section()
    rag_config_instance.vector_store = MagicMock()
    nvidia_rag_config_cls.return_value = rag_config_instance

    # Build the module tree
    nvidia_rag_mod = ModuleType("nvidia_rag")
    rag_server = ModuleType("nvidia_rag.rag_server")
    rag_server_main = ModuleType("nvidia_rag.rag_server.main")
    utils = ModuleType("nvidia_rag.utils")
    utils_config = ModuleType("nvidia_rag.utils.configuration")
    rag_server_main.NvidiaRAG = nvidia_rag_cls
    utils_config.NvidiaRAGConfig = nvidia_rag_config_cls

    monkeypatch.setitem(sys.modules, "nvidia_rag", nvidia_rag_mod)
    monkeypatch.setitem(sys.modules, "nvidia_rag.rag_server", rag_server)
    monkeypatch.setitem(sys.modules, "nvidia_rag.rag_server.main", rag_server_main)
    monkeypatch.setitem(sys.modules, "nvidia_rag.utils", utils)
    monkeypatch.setitem(sys.modules, "nvidia_rag.utils.configuration", utils_config)
    return nvidia_rag_cls, nvidia_rag_config_cls, rag_config_instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRagLibImport:
    """The adapter raises a friendly ImportError when nvidia-rag isn't installed."""

    def test_missing_package_raises_clear_error(self, monkeypatch):
        # Ensure no fake injection — these modules must NOT be in sys.modules.
        for mod in (
            "nvidia_rag",
            "nvidia_rag.rag_server",
            "nvidia_rag.rag_server.main",
            "nvidia_rag.utils",
            "nvidia_rag.utils.configuration",
        ):
            monkeypatch.delitem(sys.modules, mod, raising=False)

        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        with pytest.raises(ImportError, match=r"vss-agents\[rag_lib\]"):
            RagLibAdapter(RagLibConfig())


class TestRagLibConfigOverrides:
    """Caller-supplied overrides flow through to NvidiaRAGConfig."""

    def test_llm_url_and_model_override(self, fake_nvidia_rag):
        _, _, rag_config = fake_nvidia_rag
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        RagLibAdapter(RagLibConfig(llm_base_url="http://nim:8000", llm_model_name="meta/llama-3.1-70b"))

        rag_config.llm.model_copy.assert_called_once_with(
            update={"server_url": "http://nim:8000", "model_name": "meta/llama-3.1-70b"}
        )

    def test_embedder_url_and_model_override(self, fake_nvidia_rag):
        _, _, rag_config = fake_nvidia_rag
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        RagLibAdapter(
            RagLibConfig(embedder_base_url="http://embed:8000", embedder_model_name="nvidia/nv-embedqa-e5-v5")
        )

        rag_config.embeddings.model_copy.assert_called_once_with(
            update={"server_url": "http://embed:8000", "model_name": "nvidia/nv-embedqa-e5-v5"}
        )

    def test_milvus_uri_override(self, fake_nvidia_rag):
        _, _, rag_config = fake_nvidia_rag
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        RagLibAdapter(RagLibConfig(milvus_uri="http://milvus:19530"))

        assert rag_config.vector_store.url == "http://milvus:19530"

    def test_pipeline_toggles(self, fake_nvidia_rag):
        _, _, rag_config = fake_nvidia_rag
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        RagLibAdapter(RagLibConfig(enable_citations=False, enable_guardrails=True))

        assert rag_config.enable_citations is False
        assert rag_config.enable_guardrails is True


class TestRagLibRetrieve:
    """retrieve() builds the right search kwargs and routes citations through
    the shared frag_api normaliser."""

    def _make_adapter(self, fake_nvidia_rag, **kwargs):
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        return RagLibAdapter(RagLibConfig(**kwargs))

    @pytest.mark.asyncio
    async def test_search_kwargs_shape(self, fake_nvidia_rag):
        adapter = self._make_adapter(fake_nvidia_rag, collection_name="default_collection")
        adapter._rag_client.search = AsyncMock(return_value=MagicMock(results=[]))

        await adapter.retrieve(query="what is x", collection_name="warehouse", top_k=5)

        kwargs = adapter._rag_client.search.call_args.kwargs
        assert kwargs["query"] == "what is x"
        assert kwargs["collection_names"] == ["warehouse"]
        assert kwargs["reranker_top_k"] == 5

    @pytest.mark.asyncio
    async def test_empty_collection_falls_back_to_config_default(self, fake_nvidia_rag):
        adapter = self._make_adapter(fake_nvidia_rag, collection_name="my_default")
        adapter._rag_client.search = AsyncMock(return_value=MagicMock(results=[]))

        await adapter.retrieve(query="q", collection_name="")

        assert adapter._rag_client.search.call_args.kwargs["collection_names"] == ["my_default"]

    @pytest.mark.asyncio
    async def test_dict_filter_pushed_down_as_filter_expr(self, fake_nvidia_rag):
        adapter = self._make_adapter(fake_nvidia_rag)
        adapter._rag_client.search = AsyncMock(return_value=MagicMock(results=[]))

        await adapter.retrieve(
            query="q",
            collection_name="c",
            filters={"filter_expr": 'content_metadata["filename"] == "F.pdf"'},
        )

        assert adapter._rag_client.search.call_args.kwargs["filter_expr"] == 'content_metadata["filename"] == "F.pdf"'

    @pytest.mark.asyncio
    async def test_search_exception_returns_failure_result(self, fake_nvidia_rag):
        adapter = self._make_adapter(fake_nvidia_rag)
        adapter._rag_client.search = AsyncMock(side_effect=RuntimeError("milvus unreachable"))

        result = await adapter.retrieve(query="q", collection_name="c")

        assert result.success is False
        assert "milvus unreachable" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_callable_filter_applied_client_side(self, fake_nvidia_rag):
        from lib.knowledge.schema import Chunk

        adapter = self._make_adapter(fake_nvidia_rag)
        # Two fake citations — caller's predicate keeps only the first.
        keep = MagicMock()
        keep.model_dump.return_value = {"document_name": "A.pdf", "content": "keep", "score": 0.9}
        drop = MagicMock()
        drop.model_dump.return_value = {"document_name": "B.pdf", "content": "drop", "score": 0.8}
        adapter._rag_client.search = AsyncMock(return_value=MagicMock(results=[keep, drop]))

        result = await adapter.retrieve(
            query="q",
            collection_name="c",
            filters=lambda chunk: chunk.metadata.get("file_name") == "A.pdf",
        )

        assert all(isinstance(c, Chunk) for c in result.chunks)
        assert [c.metadata["file_name"] for c in result.chunks] == ["A.pdf"]


class TestRagLibHealth:
    @pytest.mark.asyncio
    async def test_health_check_true_after_init(self, fake_nvidia_rag):
        from lib.knowledge.adapters.rag_lib import RagLibAdapter
        from lib.knowledge.adapters.rag_lib import RagLibConfig

        adapter = RagLibAdapter(RagLibConfig())
        assert await adapter.health_check() is True
