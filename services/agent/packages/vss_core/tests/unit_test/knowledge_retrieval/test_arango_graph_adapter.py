# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for vss_core.knowledge.adapters.arango_graph.

The graph packages are optional. These tests inject fake LangChain graph
modules so the adapter contract is covered without requiring live graph DBs.
"""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_arango(monkeypatch):
    arango_mod = ModuleType("arango")
    client_instance = MagicMock(name="arango_client")
    db = MagicMock(name="arango_db")
    client_instance.db.return_value = db
    client_cls = MagicMock(name="ArangoClient", return_value=client_instance)
    arango_mod.ArangoClient = client_cls
    monkeypatch.setitem(sys.modules, "arango", arango_mod)

    community_mod = ModuleType("langchain_community")
    graphs_mod = ModuleType("langchain_community.graphs")
    graph_cls = MagicMock(name="ArangoGraph")
    graph_cls.side_effect = [TypeError("unexpected keyword argument 'graph_name'"), MagicMock(name="arango_graph")]
    graphs_mod.ArangoGraph = graph_cls
    monkeypatch.setitem(sys.modules, "langchain_community", community_mod)
    monkeypatch.setitem(sys.modules, "langchain_community.graphs", graphs_mod)

    classic_mod = ModuleType("langchain_classic")
    chains_mod = ModuleType("langchain_classic.chains")
    chain = MagicMock(name="arango_chain")
    chain.invoke.return_value = {
        "result": "node_b_1 is related to node_a_1.",
        "aql_query": "FOR n IN node_b RETURN n",
    }
    chain_cls = MagicMock(name="ArangoGraphQAChain")
    chain_cls.from_llm.return_value = chain
    chains_mod.ArangoGraphQAChain = chain_cls
    monkeypatch.setitem(sys.modules, "langchain_classic", classic_mod)
    monkeypatch.setitem(sys.modules, "langchain_classic.chains", chains_mod)
    return SimpleNamespace(
        llm=MagicMock(name="resolved_llm"),
        client_cls=client_cls,
        client_instance=client_instance,
        db=db,
        graph_cls=graph_cls,
        chain_cls=chain_cls,
        chain=chain,
    )


@pytest.fixture
def functional_arango(monkeypatch):
    arango_mod = ModuleType("arango")
    client_instance = MagicMock(name="arango_client")
    db = MagicMock(name="arango_db")
    client_instance.db.return_value = db
    client_cls = MagicMock(name="ArangoClient", return_value=client_instance)
    arango_mod.ArangoClient = client_cls
    monkeypatch.setitem(sys.modules, "arango", arango_mod)

    state = SimpleNamespace(graph=None, chain=None)

    class FakeArangoGraph:
        schema = "node_a -> related_to -> node_b"

        def __init__(self, db, graph_name=None):
            self.db = db
            self.graph_name = graph_name
            self.queries = []
            state.graph = self

        def query(self, aql_query):
            self.queries.append(aql_query)
            assert "FOR a IN node_a" in aql_query
            assert 'FILTER a._key == "node_a_1"' in aql_query
            return [
                {
                    "source": "node_a_1",
                    "related": "node_b_1",
                    "evidence": "node_a_1 connects to node_b_1 through related_to.",
                }
            ]

    community_mod = ModuleType("langchain_community")
    graphs_mod = ModuleType("langchain_community.graphs")
    graphs_mod.ArangoGraph = FakeArangoGraph
    monkeypatch.setitem(sys.modules, "langchain_community", community_mod)
    monkeypatch.setitem(sys.modules, "langchain_community.graphs", graphs_mod)

    class FakeGraphQAChain:
        output_key = "result"

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.llm = kwargs["llm"]
            self.graph = kwargs["graph"]
            self.aql_examples = kwargs["aql_examples"]
            state.chain = self

        @classmethod
        def from_llm(cls, **kwargs):
            return cls(**kwargs)

        def invoke(self, payload):
            query = payload["query"]
            aql_query = self.llm.invoke(
                {
                    "task": "generate_aql",
                    "schema": self.graph.schema,
                    "aql_examples": self.aql_examples,
                    "query": query,
                }
            )
            aql_result = self.graph.query(aql_query)
            answer = self.llm.invoke(
                {
                    "task": "answer",
                    "query": query,
                    "aql_query": aql_query,
                    "aql_result": aql_result,
                }
            )
            result = {self.output_key: answer}
            if self.kwargs["return_aql_query"]:
                result["aql_query"] = aql_query
            if self.kwargs["return_aql_result"]:
                result["aql_result"] = aql_result
            return result

    classic_mod = ModuleType("langchain_classic")
    chains_mod = ModuleType("langchain_classic.chains")
    chains_mod.ArangoGraphQAChain = FakeGraphQAChain
    monkeypatch.setitem(sys.modules, "langchain_classic", classic_mod)
    monkeypatch.setitem(sys.modules, "langchain_classic.chains", chains_mod)

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, payload):
            self.calls.append(payload)
            if payload["task"] == "generate_aql":
                assert "Example graph" in payload["aql_examples"]
                assert "example_relationship" in payload["aql_examples"]
                assert "node_a --related_to-> node_b" in payload["aql_examples"]
                return (
                    "FOR a IN node_a\n"
                    '  FILTER a._key == "node_a_1"\n'
                    "  FOR edge IN related_to\n"
                    "    FILTER edge._from == a._id\n"
                    "    RETURN edge"
                )
            return "node_b_1 is related to node_a_1 through related_to."

    return SimpleNamespace(
        llm=FakeLLM(),
        client_cls=client_cls,
        client_instance=client_instance,
        db=db,
        state=state,
    )


class TestArangoGraphImport:
    def test_missing_resolved_llm_raises_clear_error(self):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        with pytest.raises(ValueError, match=r"resolved LangChain LLM"):
            ArangoGraphAdapter(ArangoGraphConfig())

    def test_missing_arango_package_raises_clear_error(self, monkeypatch):
        for mod in ("arango", "langchain_community.graphs", "langchain_classic.chains"):
            monkeypatch.setitem(sys.modules, mod, None)

        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        with pytest.raises(ImportError, match=r"python-arango"):
            ArangoGraphAdapter(ArangoGraphConfig(), llm=MagicMock(name="resolved_llm"))


class TestArangoGraphArango:
    def test_constructs_arango_chain(self, fake_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        ArangoGraphAdapter(
            ArangoGraphConfig(
                arango_url="http://arango:8529",
                arango_database="example_graphs",
                arango_username="root",
                arango_password="pw",
                arango_graph_name="example_graph",
                schema_description="node_a vertices connect to node_b vertices.",
                allowed_vertex_collections=["node_a", "node_b"],
                allowed_edge_collections=["related_to"],
                graph_semantics=[
                    {
                        "name": "example_relationship",
                        "user_terms": ["related", "relationship", "connection"],
                        "source_collection": "node_a",
                        "edge_collection": "related_to",
                        "target_collection": "node_b",
                        "meaning": "node_a records are connected to related node_b records.",
                    }
                ],
            ),
            llm=fake_arango.llm,
        )

        fake_arango.client_cls.assert_called_once_with(hosts="http://arango:8529")
        fake_arango.client_instance.db.assert_called_once_with(
            "example_graphs",
            username="root",
            password="pw",
        )
        assert fake_arango.graph_cls.call_args_list[0].kwargs == {"graph_name": "example_graph"}
        assert fake_arango.graph_cls.call_args_list[1].args == (fake_arango.db,)
        fake_arango.chain_cls.from_llm.assert_called_once()
        chain_kwargs = fake_arango.chain_cls.from_llm.call_args.kwargs
        assert chain_kwargs["allow_dangerous_requests"] is True
        assert "node_a vertices connect to node_b vertices." in chain_kwargs["aql_examples"]
        assert "Allowed vertex collections: ['node_a', 'node_b']" in chain_kwargs["aql_examples"]
        assert "Allowed edge collections: ['related_to']" in chain_kwargs["aql_examples"]
        assert "example_relationship" in chain_kwargs["aql_examples"]
        assert "node_a --related_to-> node_b" in chain_kwargs["aql_examples"]
        assert "node_a records are connected to related node_b records" in chain_kwargs["aql_examples"]

    @pytest.mark.asyncio
    async def test_arango_retrieve_preserves_metadata(self, fake_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        adapter = ArangoGraphAdapter(ArangoGraphConfig(), llm=fake_arango.llm)

        result = await adapter.retrieve(query="Which nodes are related?", collection_name="example_graph")

        assert result.success is True
        chunk = result.chunks[0]
        assert "node_b_1" in chunk.content
        assert chunk.metadata["graph_provider"] == "arango_graph"
        assert chunk.metadata["collection_name"] == "example_graph"
        assert chunk.metadata["source_metadata"]["aql_query"] == "FOR n IN node_b RETURN n"

    def test_arango_config_can_override_env_defaults(self, fake_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        ArangoGraphAdapter(
            ArangoGraphConfig(
                arango_url="http://custom-arango:8529",
                arango_database="custom_db",
                arango_username="custom_user",
                arango_password="custom_pw",
                arango_graph_name="custom_graph",
            ),
            llm=fake_arango.llm,
        )

        fake_arango.client_cls.assert_called_once_with(hosts="http://custom-arango:8529")
        fake_arango.client_instance.db.assert_called_once_with(
            "custom_db",
            username="custom_user",
            password="custom_pw",
        )
        assert fake_arango.graph_cls.call_args_list[0].kwargs == {"graph_name": "custom_graph"}

    def test_arango_chain_passes_query_guidance(self, fake_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        ArangoGraphAdapter(
            ArangoGraphConfig(
                arango_aql_examples="FOR a IN node_a LIMIT 5 RETURN a",
                max_aql_generation_attempts=4,
                return_intermediate_steps=True,
            ),
            llm=fake_arango.llm,
        )

        chain_kwargs = fake_arango.chain_cls.from_llm.call_args.kwargs
        assert "AQL examples:" in chain_kwargs["aql_examples"]
        assert "FOR a IN node_a LIMIT 5 RETURN a" in chain_kwargs["aql_examples"]
        assert chain_kwargs["return_aql_query"] is True
        assert chain_kwargs["return_aql_result"] is True
        assert chain_kwargs["max_aql_generation_attempts"] == 4

    def test_arango_chain_allows_no_semantics_or_examples(self, fake_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        ArangoGraphAdapter(ArangoGraphConfig(), llm=fake_arango.llm)

        chain_kwargs = fake_arango.chain_cls.from_llm.call_args.kwargs
        assert chain_kwargs["aql_examples"] == ""

    def test_arango_config_rejects_unknown_fields(self, fake_arango):
        from pydantic import ValidationError

        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        with pytest.raises(ValidationError, match="extra_forbidden"):
            ArangoGraphAdapter(
                ArangoGraphConfig(arango_database="example_graphs", unexpected=True),
                llm=fake_arango.llm,
            )

    @pytest.mark.asyncio
    async def test_arango_retrieve_runs_aql_generation_execution_and_answer_flow(self, functional_arango):
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphAdapter
        from vss_core.knowledge.adapters.arango_graph import ArangoGraphConfig

        adapter = ArangoGraphAdapter(
            ArangoGraphConfig(
                arango_url="http://arango:8529",
                arango_database="example_graphs",
                arango_graph_name="example_graph",
                schema_description="Example graph. node_a vertices connect to node_b vertices.",
                graph_semantics=[
                    {
                        "name": "example_relationship",
                        "user_terms": ["related", "relationship", "connection"],
                        "source_collection": "node_a",
                        "edge_collection": "related_to",
                        "target_collection": "node_b",
                        "meaning": "node_a records are connected to related node_b records.",
                    }
                ],
            ),
            llm=functional_arango.llm,
        )

        result = await adapter.retrieve(
            query="Which node_b records are related to node_a_1?",
            collection_name="example_graph",
            top_k=3,
            filters={"entity_id": "node_a_1"},
        )

        assert result.success is True
        assert result.summary == "node_b_1 is related to node_a_1 through related_to."
        chunk = result.chunks[0]
        assert chunk.content == result.summary
        assert chunk.metadata["graph_provider"] == "arango_graph"
        assert chunk.metadata["collection_name"] == "example_graph"
        assert chunk.metadata["source_metadata"]["aql_query"].startswith("FOR a IN node_a")
        assert chunk.metadata["source_metadata"]["aql_result"] == [
            {
                "source": "node_a_1",
                "related": "node_b_1",
                "evidence": "node_a_1 connects to node_b_1 through related_to.",
            }
        ]
        assert functional_arango.state.graph.graph_name == "example_graph"
        assert functional_arango.state.graph.queries == [chunk.metadata["source_metadata"]["aql_query"]]
        assert functional_arango.llm.calls[0]["task"] == "generate_aql"
        assert "entity_id: 'node_a_1'" in functional_arango.llm.calls[0]["query"]
        assert "Return at most 3" in functional_arango.llm.calls[0]["query"]
        assert functional_arango.llm.calls[1]["task"] == "answer"
        assert functional_arango.llm.calls[1]["aql_result"][0]["related"] == "node_b_1"


class TestArangoGraphHelpers:
    def test_string_result_supported(self):
        from vss_core.knowledge.adapters.arango_graph import _normalise_chain_result

        answer, metadata = _normalise_chain_result("plain answer")
        assert answer == "plain answer"
        assert metadata == {}

    def test_invoke_chain_uses_langchain_invoke(self):
        from vss_core.knowledge.adapters.arango_graph import _invoke_chain

        chain = MagicMock(name="chain")
        chain.invoke.return_value = {"result": "node_b_1 is related."}

        result = _invoke_chain(chain, "Find related nodes.")

        assert result["result"] == "node_b_1 is related."
        chain.invoke.assert_called_once_with({"query": "Find related nodes."})
