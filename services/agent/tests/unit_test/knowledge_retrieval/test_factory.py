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
"""Tests for lib.knowledge.factory.

Covers behaviour that isn't trivially obvious from the source: singleton
caching keyed on (backend, config), lazy-import triggering on first lookup,
and recursive config-key normalisation.
"""

import pytest

from lib.knowledge import factory
from lib.knowledge.base import BackendAdapter
from lib.knowledge.factory import _freeze, get_retriever, register_adapter
from lib.knowledge.schema import RetrievalResult


class _StubAdapter(BackendAdapter):
    @property
    def backend_name(self) -> str:
        return "_stub"

    async def retrieve(self, query, collection_name, top_k=5, filters=None):
        return RetrievalResult(query=query, backend="_stub")

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot/restore module-level registry so tests don't leak state."""
    saved_registry = dict(factory._registry)
    saved_instances = dict(factory._instances)
    try:
        yield
    finally:
        factory._registry.clear()
        factory._registry.update(saved_registry)
        factory._instances.clear()
        factory._instances.update(saved_instances)


class TestGetRetriever:
    """Singleton + dispatch behaviour of the factory."""

    def test_returns_singleton_for_same_config(self):
        register_adapter("_stub")(_StubAdapter)
        a = get_retriever("_stub", {"x": 1})
        b = get_retriever("_stub", {"x": 1})
        assert a is b

    def test_separate_instances_for_distinct_configs(self):
        register_adapter("_stub")(_StubAdapter)
        a = get_retriever("_stub", {"x": 1})
        b = get_retriever("_stub", {"x": 2})
        assert a is not b

    def test_unknown_backend_raises_with_available_list(self):
        with pytest.raises(ValueError) as excinfo:
            get_retriever("does_not_exist", {})
        msg = str(excinfo.value)
        assert "does_not_exist" in msg
        # Lazy-import map is included in the available list — frag_api ships by default.
        assert "frag_api" in msg

    def test_lazy_import_registers_on_first_lookup(self):
        # frag_api lives in _LAZY_BACKENDS but isn't pre-imported. The factory
        # must trigger its import the first time it's requested.
        adapter = get_retriever(
            "frag_api",
            {"rag_url": "http://localhost:8081/v1", "timeout": 5, "verify_ssl": True},
        )
        assert adapter.backend_name == "frag_api"


class TestFreeze:
    """Cache-key normaliser — non-trivial because it must handle nested dicts
    and lists so configs differing only in iteration order share a cache key."""

    def test_nested_dict_order_independent(self):
        assert _freeze({"top": {"a": 1, "b": 2}}) == _freeze({"top": {"b": 2, "a": 1}})

    def test_list_value_normalised(self):
        assert _freeze({"xs": [1, 2, 3]}) == _freeze({"xs": [1, 2, 3]})
