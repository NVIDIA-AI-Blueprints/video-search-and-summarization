# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for lib.knowledge.adapters.langchain.

The `langchain-chroma` / `langchain-nvidia-ai-endpoints` packages are an
optional extra (`vss-agents[langchain]`) and aren't installed in the default
test env. The adapter does deferred imports inside `__init__`, so we inject
fake modules into `sys.modules` BEFORE constructing the adapter — the
deferred import resolves to our fakes and we never need the real packages.
"""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fake langchain_chroma + langchain_nvidia_ai_endpoints injection
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_langchain(monkeypatch):
    """Install fake langchain_chroma / langchain_nvidia_ai_endpoints modules.

    Returns a SimpleNamespace exposing the key mocks the tests need to wire:
      - chroma_cls — Chroma class mock (the per-collection vector store)
      - chroma_instance — the vector store instance returned from Chroma(...)
      - nvidia_embeddings_cls — NVIDIAEmbeddings class mock
    """
    chroma_mod = ModuleType("langchain_chroma")
    chroma_instance = MagicMock(name="chroma_instance")
    chroma_instance.asimilarity_search_with_score = AsyncMock(return_value=[])
    chroma_cls = MagicMock(name="Chroma_cls", return_value=chroma_instance)
    chroma_mod.Chroma = chroma_cls

    nvidia_mod = ModuleType("langchain_nvidia_ai_endpoints")
    nvidia_embeddings_cls = MagicMock(name="NVIDIAEmbeddings_cls")
    nvidia_mod.NVIDIAEmbeddings = nvidia_embeddings_cls

    monkeypatch.setitem(sys.modules, "langchain_chroma", chroma_mod)
    monkeypatch.setitem(sys.modules, "langchain_nvidia_ai_endpoints", nvidia_mod)

    return SimpleNamespace(
        chroma_cls=chroma_cls,
        chroma_instance=chroma_instance,
        nvidia_embeddings_cls=nvidia_embeddings_cls,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLangChainImport:
    """ImportError when the extra isn't installed surfaces a clear, actionable hint."""

    def test_missing_packages_raises_clear_error(self, monkeypatch):
        # Setting sys.modules[mod] = None makes `import mod` raise ModuleNotFoundError,
        # independent of whether the package is actually installed in the venv.
        for mod in ("langchain_chroma", "langchain_nvidia_ai_endpoints"):
            monkeypatch.setitem(sys.modules, mod, None)

        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        with pytest.raises(ImportError, match=r"vss-agents\[langchain\]"):
            LangChainAdapter(LangChainConfig(persist_dir="/tmp/chroma"))


class TestLangChainConfigDefaults:
    """`persist_dir` resolution — env var override > literal default."""

    def test_default_falls_back_to_tmp_chroma_data(self, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainConfig

        monkeypatch.delenv("VSS_CHROMA_DIR", raising=False)
        assert LangChainConfig().persist_dir == "/tmp/chroma_data"

    def test_env_var_overrides_default(self, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainConfig

        monkeypatch.setenv("VSS_CHROMA_DIR", "/var/lib/chroma")
        assert LangChainConfig().persist_dir == "/var/lib/chroma"

    def test_explicit_value_overrides_env(self, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainConfig

        monkeypatch.setenv("VSS_CHROMA_DIR", "/var/lib/chroma")
        assert LangChainConfig(persist_dir="/custom/path").persist_dir == "/custom/path"


class TestLangChainInit:
    """Construction wires NVIDIAEmbeddings and caches the Chroma class."""

    def test_persist_dir_created_on_init(self, fake_langchain, tmp_path):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        persist = tmp_path / "fresh"
        assert not persist.exists()
        LangChainAdapter(LangChainConfig(persist_dir=str(persist)))
        # `__init__` auto-creates the persist dir so a clean host doesn't crash.
        assert persist.is_dir()

    def test_embed_api_key_env_fallback(self, fake_langchain, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        monkeypatch.setenv("NVIDIA_API_KEY", "env-key-123")
        LangChainAdapter(LangChainConfig(persist_dir="/tmp/chroma"))

        kwargs = fake_langchain.nvidia_embeddings_cls.call_args.kwargs
        assert kwargs["api_key"] == "env-key-123"

    def test_embed_api_key_config_overrides_env(self, fake_langchain, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        monkeypatch.setenv("NVIDIA_API_KEY", "env-key-123")
        LangChainAdapter(
            LangChainConfig(persist_dir="/tmp/chroma", embed_api_key="config-key-456")  # pragma: allowlist secret
        )

        kwargs = fake_langchain.nvidia_embeddings_cls.call_args.kwargs
        assert kwargs["api_key"] == "config-key-456"


class TestLangChainRetrieve:
    """retrieve() walks LangChain's vector store API and converts Documents to Chunks."""

    def _make_adapter(self, fake_langchain, **kwargs):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        defaults = {"persist_dir": "/tmp/chroma"}
        defaults.update(kwargs)
        return LangChainAdapter(LangChainConfig(**defaults))

    @pytest.mark.asyncio
    async def test_uses_top_k_and_collection(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain, collection_name="default_coll")
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = []

        await adapter.retrieve(query="what is x", collection_name="docs", top_k=7)

        # Chroma class invoked with the requested collection_name.
        kwargs = fake_langchain.chroma_cls.call_args.kwargs
        assert kwargs["collection_name"] == "docs"
        assert kwargs["persist_directory"] == "/tmp/chroma"
        # Right top_k passed through asimilarity_search_with_score.
        fake_langchain.chroma_instance.asimilarity_search_with_score.assert_called_once_with("what is x", k=7)

    @pytest.mark.asyncio
    async def test_empty_collection_falls_back_to_config_default(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain, collection_name="my_default")
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = []

        await adapter.retrieve(query="q", collection_name="")

        kwargs = fake_langchain.chroma_cls.call_args.kwargs
        assert kwargs["collection_name"] == "my_default"

    @pytest.mark.asyncio
    async def test_vectorstore_cached_per_collection(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain, collection_name="default_coll")
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = []

        await adapter.retrieve(query="q1", collection_name="docs")
        await adapter.retrieve(query="q2", collection_name="docs")

        # Same collection -> one Chroma() construction.
        assert fake_langchain.chroma_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_document_conversion(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain)

        # Build a fake LangChain Document. score=0.13 cosine distance -> sim=0.87.
        doc = MagicMock()
        doc.page_content = "this is the chunk content"
        doc.metadata = {"file_name": "Manual.pdf", "page": 3, "id": "node-001"}
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = [(doc, 0.13)]

        result = await adapter.retrieve(query="q", collection_name="c", top_k=5)

        assert result.success is True
        assert len(result.chunks) == 1
        chunk = result.chunks[0]
        assert chunk.content == "this is the chunk content"
        # Cosine distance 0.13 -> similarity 0.87.
        assert chunk.score == pytest.approx(0.87)
        assert chunk.chunk_id == "node-001"
        assert chunk.metadata["file_name"] == "Manual.pdf"
        assert chunk.metadata["page_number"] == 3
        assert chunk.metadata["display_citation"] == "[Manual.pdf, p.3]"

    @pytest.mark.asyncio
    async def test_retrieve_exception_maps_to_failure(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain)
        fake_langchain.chroma_instance.asimilarity_search_with_score.side_effect = RuntimeError("chroma down")

        result = await adapter.retrieve(query="q", collection_name="c")

        assert result.success is False
        assert "chroma down" in (result.error_message or "")
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_callable_filter_applied_client_side(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain)

        def _make_doc(name: str, text: str):
            doc = MagicMock()
            doc.page_content = text
            doc.metadata = {"file_name": name, "page": 1}
            return doc

        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = [
            (_make_doc("keep.pdf", "keep"), 0.2),
            (_make_doc("drop.pdf", "drop"), 0.2),
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
            (0, 1, False),  # under floor -> 1
            (5, 20, True),  # filter triggers 4x overfetch
            (80, 100, True),  # overfetch would exceed MAX -> clamp
        ],
    )
    async def test_top_k_resolution(self, fake_langchain, input_top_k, expected_k, with_filter):
        adapter = self._make_adapter(fake_langchain)
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = []
        filters = (lambda _c: True) if with_filter else None

        await adapter.retrieve(query="q", collection_name="c", top_k=input_top_k, filters=filters)

        fake_langchain.chroma_instance.asimilarity_search_with_score.assert_called_once_with("q", k=expected_k)

    @pytest.mark.asyncio
    async def test_filter_result_trimmed_to_top_k(self, fake_langchain):
        adapter = self._make_adapter(fake_langchain)

        def _make_doc(name: str):
            doc = MagicMock()
            doc.page_content = "x"
            doc.metadata = {"file_name": name, "page": 1}
            return doc

        # 6 candidates pass the filter; top_k=2 -> trimmed to 2.
        fake_langchain.chroma_instance.asimilarity_search_with_score.return_value = [
            (_make_doc(f"d{i}.pdf"), 0.1) for i in range(6)
        ]

        result = await adapter.retrieve(
            query="q",
            collection_name="c",
            top_k=2,
            filters=lambda _c: True,
        )

        assert len(result.chunks) == 2

    def test_score_passthrough_when_outside_distance_range(self):
        # raw_score > 2 is treated as already-similarity; no 1 - x normalization.
        from lib.knowledge.adapters.langchain import _doc_to_chunk

        doc = MagicMock()
        doc.page_content = "x"
        doc.metadata = {"file_name": "a.pdf"}
        chunk = _doc_to_chunk(doc, score=5.7)
        assert chunk is not None
        assert chunk.score == pytest.approx(5.7)


class TestLangChainHealth:
    @pytest.mark.asyncio
    async def test_health_check_true_when_persist_dir_exists(self, fake_langchain, tmp_path):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        adapter = LangChainAdapter(LangChainConfig(persist_dir=str(tmp_path)))
        assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false_when_persist_dir_missing(self, fake_langchain, tmp_path, monkeypatch):
        from lib.knowledge.adapters.langchain import LangChainAdapter
        from lib.knowledge.adapters.langchain import LangChainConfig

        adapter = LangChainAdapter(LangChainConfig(persist_dir=str(tmp_path)))
        # Simulate the persist dir being yanked out from under us after construction.
        tmp_path.rmdir()
        assert await adapter.health_check() is False
