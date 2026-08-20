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
"""Tests for vss_core.knowledge.adapters.db_query."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from vss_core.knowledge.adapters.db_query import DbQueryAdapter
from vss_core.knowledge.adapters.db_query import DbQueryConfig
from vss_core.knowledge.adapters.db_query import _extract_params
from vss_core.knowledge.adapters.db_query import _parse_query
from vss_core.knowledge.adapters.db_query import build_ca_rag_db_query_config


class TestDbQueryConfig:
    def test_defaults(self) -> None:
        config = DbQueryConfig()
        assert config.db_type == "neo4j"
        assert config.db_host == "localhost"
        assert config.db_port == "7687"  # default port for neo4j
        assert config.embedding_enable is False

    def test_default_port_follows_db_type(self) -> None:
        assert DbQueryConfig(db_type="arango").db_port == "8529"
        assert DbQueryConfig(db_type="milvus").db_port == "19530"
        assert DbQueryConfig(db_type="elasticsearch").db_port == "9200"
        assert DbQueryConfig(db_type="arango", db_port="9999").db_port == "9999"
        assert DbQueryConfig(db_type="arango", db_port=8529).db_port == "8529"  # type: ignore[arg-type]

    def test_rejects_unknown_db_type(self) -> None:
        with pytest.raises(Exception):
            DbQueryConfig(db_type="postgres")  # type: ignore[arg-type]


class TestBuildCaRagConfig:
    def test_minimal_neo4j_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        config = DbQueryConfig(
            db_type="neo4j",
            db_host="graph-db",
            db_port="7687",
            db_user="neo4j",
            db_password="secret",
            uuid="stream-1",
        )
        ca = build_ca_rag_db_query_config(config)
        assert ca["context_manager"] == {"functions": ["db_query"], "uuid": "stream-1"}
        assert ca["functions"]["db_query"]["type"] == "db_query"
        assert ca["tools"]["db"]["type"] == "neo4j"
        assert ca["tools"]["db"]["params"]["host"] == "graph-db"
        assert "collection_name" not in ca["tools"]["db"]["params"]
        assert ca["tools"]["nvidia_embedding"]["params"]["enable"] is False
        assert ca["tools"]["nvidia_embedding"]["params"]["api_key"] == "test-key"

    def test_collection_name_and_arango(self) -> None:
        config = DbQueryConfig(
            db_type="arango",
            collection_name="example_graphs",
            embedding_enable=True,
        )
        ca = build_ca_rag_db_query_config(config)
        assert ca["tools"]["db"]["type"] == "arango"
        assert ca["tools"]["db"]["params"]["collection_name"] == "example_graphs"
        assert ca["tools"]["nvidia_embedding"]["params"]["enable"] is True


class TestParseQueryAndParams:
    def test_elasticsearch_json_body(self) -> None:
        body = '{"query": {"match_all": {}}}'
        parsed = _parse_query(body, "elasticsearch")
        assert parsed == {"query": {"match_all": {}}}

    def test_non_es_left_as_string(self) -> None:
        assert _parse_query('{"a": 1}', "neo4j") == '{"a": 1}'

    def test_params_nested_or_flat(self) -> None:
        assert _extract_params({"params": {"limit": 1}}) == {"limit": 1}
        assert _extract_params({"limit": 1, "skip": 0}) == {"limit": 1, "skip": 0}
        assert _extract_params(None) == {}


@pytest.mark.asyncio
async def test_retrieve_success_via_injected_handler() -> None:
    handler = MagicMock()
    handler.call = AsyncMock(return_value={"db_query": {"result": [{"n": 42}]}})
    adapter = DbQueryAdapter(
        DbQueryConfig(db_type="neo4j", schema_description="Nodes: Person"),
        handler=handler,
    )
    assert "Person" in adapter.tool_description_hint
    assert "db_type='neo4j'" in adapter.tool_description_hint
    assert "Cypher" in adapter.tool_description_hint
    assert "Never pass English" in adapter.tool_description_hint

    result = await adapter.retrieve(
        query="MATCH (n) RETURN n LIMIT $limit",
        collection_name="",
        top_k=5,
        filters={"params": {"limit": 5}},
    )

    assert result.success is True
    assert len(result.chunks) == 1
    assert "42" in result.chunks[0].content
    assert result.chunks[0].metadata["db_type"] == "neo4j"
    handler.call.assert_awaited_once_with(
        {
            "db_query": {
                "query": "MATCH (n) RETURN n LIMIT $limit",
                "params": {"limit": 5},
            }
        }
    )


@pytest.mark.asyncio
async def test_retrieve_surfaces_function_error() -> None:
    handler = MagicMock()
    handler.call = AsyncMock(return_value={"db_query": {"error": "bad query"}})
    adapter = DbQueryAdapter(DbQueryConfig(), handler=handler)

    result = await adapter.retrieve(query="BAD", collection_name="", top_k=1)

    assert result.success is False
    assert result.error_message == "bad query"
    assert result.chunks == []


@pytest.mark.asyncio
async def test_retrieve_surfaces_handler_exception() -> None:
    handler = MagicMock()
    handler.call = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = DbQueryAdapter(DbQueryConfig(), handler=handler)

    result = await adapter.retrieve(query="MATCH (n) RETURN n", collection_name="", top_k=1)

    assert result.success is False
    assert "boom" in (result.error_message or "")


@pytest.mark.asyncio
async def test_elasticsearch_query_parsed_to_dict() -> None:
    handler = MagicMock()
    handler.call = AsyncMock(return_value={"db_query": {"result": []}})
    adapter = DbQueryAdapter(DbQueryConfig(db_type="elasticsearch"), handler=handler)

    await adapter.retrieve(
        query=json.dumps({"query": {"match_all": {}}}),
        collection_name="events",
        top_k=3,
    )

    call_state: dict[str, Any] = handler.call.await_args.args[0]
    assert call_state["db_query"]["query"] == {"query": {"match_all": {}}}


@pytest.mark.asyncio
async def test_milvus_retrieve_strips_vector_fields() -> None:
    from vss_core.knowledge.adapters.db_query import _strip_milvus_vectors

    assert _strip_milvus_vectors(
        [{"case_id": "case_123", "vector": [0.1, 0.2], "embedding": [1.0], "text": "x"}]
    ) == [{"case_id": "case_123", "text": "x"}]

    handler = MagicMock()
    handler.call = AsyncMock(
        return_value={
            "db_query": {
                "result": [
                    {
                        "case_id": "case_123",
                        "image_uri": "s3://demo/img.png",
                        "vector": [0.1] * 8,
                        "text": "inspection",
                    }
                ]
            }
        }
    )
    adapter = DbQueryAdapter(DbQueryConfig(db_type="milvus"), handler=handler)
    result = await adapter.retrieve(query='case_id == "case_123"', collection_name="", top_k=1)

    assert result.success is True
    content = result.chunks[0].content
    assert "case_123" in content
    assert "s3://demo/img.png" in content
    assert "vector" not in content
    assert "[0.1]" not in content
