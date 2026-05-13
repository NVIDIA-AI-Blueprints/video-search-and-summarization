# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for lib.knowledge.adapters.llama_index.

The `llama-index-*` and `chromadb` packages are an optional extra
(`vss-agents[llama_index]`) and aren't installed in the default test env.
The adapter does deferred imports inside `__init__`, so we inject fake
`llama_index.*` / `chromadb` modules into `sys.modules` BEFORE constructing
the adapter — the deferred import resolves to our fakes and we never
need the real packages.
"""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fake llama_index + chromadb injection
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llama_index(monkeypatch):
    """Install fake llama_index/chromadb modules so deferred imports resolve.

    Returns a SimpleNamespace exposing the key mocks the tests need to wire:
      - chromadb_cls.PersistentClient — Chroma client mock
      - chroma_client_instance — the singleton client object the adapter holds
      - vector_store_index_cls — LlamaIndex's VectorStoreIndex class mock
      - retriever — the mock retriever returned from index.as_retriever()
      - nvidia_embedding_cls — NVIDIAEmbedding class mock
    """
    chromadb_mod = ModuleType("chromadb")
    chroma_client_instance = MagicMock(name="chroma_client_instance")
    chroma_client_instance.list_collections = MagicMock(return_value=[])
    chroma_client_instance.get_or_create_collection = MagicMock(return_value=MagicMock(name="chroma_collection"))
    chromadb_mod.PersistentClient = MagicMock(return_value=chroma_client_instance)

    # llama_index.core: VectorStoreIndex + StorageContext
    core_mod = ModuleType("llama_index.core")
    retriever = MagicMock(name="retriever")
    retriever.aretrieve = AsyncMock(return_value=[])

    vsi_instance = MagicMock(name="VectorStoreIndex_instance")
    vsi_instance.as_retriever = MagicMock(return_value=retriever)
    vector_store_index_cls = MagicMock(name="VectorStoreIndex_cls")
    vector_store_index_cls.from_vector_store = MagicMock(return_value=vsi_instance)
    storage_context_cls = MagicMock(name="StorageContext_cls")
    storage_context_cls.from_defaults = MagicMock(return_value=MagicMock(name="storage_context"))
    core_mod.VectorStoreIndex = vector_store_index_cls
    core_mod.StorageContext = storage_context_cls

    # llama_index.embeddings.nvidia: NVIDIAEmbedding
    nvidia_emb_mod = ModuleType("llama_index.embeddings.nvidia")
    nvidia_embedding_cls = MagicMock(name="NVIDIAEmbedding_cls")
    nvidia_emb_mod.NVIDIAEmbedding = nvidia_embedding_cls

    # llama_index.vector_stores.chroma: ChromaVectorStore
    chroma_vs_mod = ModuleType("llama_index.vector_stores.chroma")
    chroma_vector_store_cls = MagicMock(name="ChromaVectorStore_cls")
    chroma_vs_mod.ChromaVectorStore = chroma_vector_store_cls

    # Parent packages need to exist too.
    llama_index_pkg = ModuleType("llama_index")
    embeddings_pkg = ModuleType("llama_index.embeddings")
    vector_stores_pkg = ModuleType("llama_index.vector_stores")

    monkeypatch.setitem(sys.modules, "chromadb", chromadb_mod)
    monkeypatch.setitem(sys.modules, "llama_index", llama_index_pkg)
    monkeypatch.setitem(sys.modules, "llama_index.core", core_mod)
    monkeypatch.setitem(sys.modules, "llama_index.embeddings", embeddings_pkg)
    monkeypatch.setitem(sys.modules, "llama_index.embeddings.nvidia", nvidia_emb_mod)
    monkeypatch.setitem(sys.modules, "llama_index.vector_stores", vector_stores_pkg)
    monkeypatch.setitem(sys.modules, "llama_index.vector_stores.chroma", chroma_vs_mod)

    return SimpleNamespace(
        chromadb_cls=chromadb_mod,
        chroma_client_instance=chroma_client_instance,
        vector_store_index_cls=vector_store_index_cls,
        retriever=retriever,
        nvidia_embedding_cls=nvidia_embedding_cls,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLlamaIndexImport:
    """ImportError when the extra isn't installed surfaces a clear, actionable hint."""

    def test_missing_packages_raises_clear_error(self, monkeypatch):
        for mod in (
            "chromadb",
            "llama_index",
            "llama_index.core",
            "llama_index.embeddings",
            "llama_index.embeddings.nvidia",
            "llama_index.vector_stores",
            "llama_index.vector_stores.chroma",
        ):
            monkeypatch.delitem(sys.modules, mod, raising=False)

        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        with pytest.raises(ImportError, match=r"vss-agents\[llama_index\]"):
            LlamaIndexAdapter(LlamaIndexConfig(persist_dir="/tmp/chroma"))


class TestLlamaIndexConfigDefaults:
    """`persist_dir` resolution — env var override > literal default."""

    def test_default_falls_back_to_tmp_chroma_data(self, monkeypatch):
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        monkeypatch.delenv("VSS_CHROMA_DIR", raising=False)
        assert LlamaIndexConfig().persist_dir == "/tmp/chroma_data"

    def test_env_var_overrides_default(self, monkeypatch):
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        monkeypatch.setenv("VSS_CHROMA_DIR", "/var/lib/chroma")
        assert LlamaIndexConfig().persist_dir == "/var/lib/chroma"

    def test_explicit_value_overrides_env(self, monkeypatch):
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        monkeypatch.setenv("VSS_CHROMA_DIR", "/var/lib/chroma")
        assert LlamaIndexConfig(persist_dir="/custom/path").persist_dir == "/custom/path"


class TestLlamaIndexInit:
    """Construction wires the PersistentClient + NVIDIA embedder."""

    def test_persistent_client_uses_persist_dir(self, fake_llama_index, tmp_path):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        LlamaIndexAdapter(LlamaIndexConfig(persist_dir=str(tmp_path)))

        fake_llama_index.chromadb_cls.PersistentClient.assert_called_once_with(path=str(tmp_path))

    def test_embed_api_key_env_fallback(self, fake_llama_index, monkeypatch):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        monkeypatch.setenv("NVIDIA_API_KEY", "env-key-123")
        LlamaIndexAdapter(LlamaIndexConfig(persist_dir="/tmp/chroma"))

        # NVIDIAEmbedding called with nvidia_api_key=env-key-123 (no override on config).
        kwargs = fake_llama_index.nvidia_embedding_cls.call_args.kwargs
        assert kwargs["nvidia_api_key"] == "env-key-123"

    def test_embed_api_key_config_overrides_env(self, fake_llama_index, monkeypatch):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        monkeypatch.setenv("NVIDIA_API_KEY", "env-key-123")
        LlamaIndexAdapter(
            LlamaIndexConfig(persist_dir="/tmp/chroma", embed_api_key="config-key-456")  # pragma: allowlist secret
        )

        kwargs = fake_llama_index.nvidia_embedding_cls.call_args.kwargs
        assert kwargs["nvidia_api_key"] == "config-key-456"


class TestLlamaIndexRetrieve:
    """retrieve() walks LlamaIndex's retriever API and converts nodes to Chunks."""

    def _make_adapter(self, fake_llama_index, **kwargs):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        defaults = {"persist_dir": "/tmp/chroma"}
        defaults.update(kwargs)
        return LlamaIndexAdapter(LlamaIndexConfig(**defaults))

    @pytest.mark.asyncio
    async def test_uses_top_k_and_collection(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index, collection_name="default_coll")
        # `retrieve()` returns no nodes — we just verify the wiring.
        fake_llama_index.retriever.aretrieve.return_value = []

        await adapter.retrieve(query="what is x", collection_name="docs", top_k=7)

        # Right Chroma collection requested.
        fake_llama_index.chroma_client_instance.get_or_create_collection.assert_called_with("docs")
        # Right top_k passed to retriever.
        fake_llama_index.vector_store_index_cls.from_vector_store.return_value.as_retriever.assert_called_with(
            similarity_top_k=7
        )
        # Query string forwarded verbatim.
        fake_llama_index.retriever.aretrieve.assert_called_once_with("what is x")

    @pytest.mark.asyncio
    async def test_empty_collection_falls_back_to_config_default(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index, collection_name="my_default")
        fake_llama_index.retriever.aretrieve.return_value = []

        await adapter.retrieve(query="q", collection_name="")

        fake_llama_index.chroma_client_instance.get_or_create_collection.assert_called_with("my_default")

    @pytest.mark.asyncio
    async def test_node_conversion(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index)

        # Build a fake NodeWithScore matching what LlamaIndex returns.
        inner = MagicMock()
        inner.text = "this is the chunk content"
        inner.node_id = "node-001"
        inner.metadata = {"file_name": "Manual.pdf", "page_label": "3", "file_path": "/docs/Manual.pdf"}
        node = MagicMock()
        node.node = inner
        node.score = 0.87
        fake_llama_index.retriever.aretrieve.return_value = [node]

        result = await adapter.retrieve(query="q", collection_name="c", top_k=5)

        assert result.success is True
        assert len(result.chunks) == 1
        chunk = result.chunks[0]
        assert chunk.content == "this is the chunk content"
        # node.score=0.87 is a cosine distance in [0, 2] -> normalised to 1 - 0.87 = 0.13.
        assert chunk.score == pytest.approx(0.13)
        assert chunk.chunk_id == "node-001"
        assert chunk.metadata["file_name"] == "Manual.pdf"
        assert chunk.metadata["page_number"] == 3
        assert chunk.metadata["display_citation"] == "[Manual.pdf, p.3]"

    @pytest.mark.asyncio
    async def test_retrieve_exception_maps_to_failure(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index)
        fake_llama_index.retriever.aretrieve.side_effect = RuntimeError("chroma down")

        result = await adapter.retrieve(query="q", collection_name="c")

        assert result.success is False
        assert "chroma down" in (result.error_message or "")
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_callable_filter_applied_client_side(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index)

        def _make_node(name: str, text: str):
            inner = MagicMock()
            inner.text = text
            inner.node_id = name
            inner.metadata = {"file_name": name, "page_label": "1"}
            node = MagicMock()
            node.node = inner
            node.score = 0.5
            return node

        fake_llama_index.retriever.aretrieve.return_value = [
            _make_node("keep.pdf", "keep"),
            _make_node("drop.pdf", "drop"),
        ]

        result = await adapter.retrieve(
            query="q",
            collection_name="c",
            filters=lambda chunk: chunk.metadata["file_name"] == "keep.pdf",
        )

        assert [c.metadata["file_name"] for c in result.chunks] == ["keep.pdf"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("input_top_k", "expected_k", "with_filter"),
        [
            (10_000, 100, False),  # over MAX_TOP_K -> clamp
            (0, 1, False),         # under floor -> 1
            (5, 20, True),         # filter triggers 4x overfetch
            (80, 100, True),       # overfetch would exceed MAX -> clamp
        ],
    )
    async def test_top_k_resolution(self, fake_llama_index, input_top_k, expected_k, with_filter):
        adapter = self._make_adapter(fake_llama_index)
        fake_llama_index.retriever.aretrieve.return_value = []
        filters = (lambda _c: True) if with_filter else None

        await adapter.retrieve(query="q", collection_name="c", top_k=input_top_k, filters=filters)

        fake_llama_index.vector_store_index_cls.from_vector_store.return_value.as_retriever.assert_called_with(
            similarity_top_k=expected_k
        )

    @pytest.mark.asyncio
    async def test_filter_result_trimmed_to_top_k(self, fake_llama_index):
        adapter = self._make_adapter(fake_llama_index)

        def _make_node(name: str):
            inner = MagicMock()
            inner.text = "x"
            inner.node_id = name
            inner.metadata = {"file_name": name, "page_label": "1"}
            node = MagicMock()
            node.node = inner
            node.score = 0.5
            return node

        # 6 candidates pass the filter; top_k=2 -> trimmed to 2.
        fake_llama_index.retriever.aretrieve.return_value = [_make_node(f"d{i}.pdf") for i in range(6)]

        result = await adapter.retrieve(
            query="q",
            collection_name="c",
            top_k=2,
            filters=lambda _c: True,
        )

        assert len(result.chunks) == 2

    def test_score_passthrough_when_outside_distance_range(self):
        # raw_score > 2 is treated as already-similarity; no 1 - x normalization.
        from lib.knowledge.adapters.llama_index import _node_to_chunk

        inner = MagicMock()
        inner.text = "x"
        inner.node_id = "n"
        inner.metadata = {"file_name": "a.pdf"}
        node = MagicMock()
        node.node = inner
        node.score = 5.7
        chunk = _node_to_chunk(node)
        assert chunk is not None
        assert chunk.score == pytest.approx(5.7)


class TestLlamaIndexHealth:
    @pytest.mark.asyncio
    async def test_health_check_true_when_chroma_reachable(self, fake_llama_index):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        adapter = LlamaIndexAdapter(LlamaIndexConfig(persist_dir="/tmp/chroma"))
        assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false_when_chroma_unreachable(self, fake_llama_index):
        from lib.knowledge.adapters.llama_index import LlamaIndexAdapter
        from lib.knowledge.adapters.llama_index import LlamaIndexConfig

        adapter = LlamaIndexAdapter(LlamaIndexConfig(persist_dir="/tmp/chroma"))
        # Simulate Chroma being unreachable after construction.
        fake_llama_index.chroma_client_instance.list_collections.side_effect = RuntimeError("unreachable")
        assert await adapter.health_check() is False
