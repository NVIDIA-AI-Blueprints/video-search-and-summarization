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
"""Shared Elasticsearch client and utilities for video analytics tools."""

from copy import deepcopy
from typing import Any
from typing import ClassVar
from typing import cast

from elasticsearch import AsyncElasticsearch

BASE_QUERY_TEMPLATE: dict[str, dict[str, dict[str, list]]] = {
    "query": {"bool": {"must": [], "filter": [], "should": [], "must_not": []}}
}


class ESClient:
    """
    Shared Elasticsearch client with common utilities.
    """

    # Whitelist of allowed indexes
    INDEXES: ClassVar[dict[str, str]] = {
        "incidents": "incidents-*",
        "vlm_incidents": "vlm-incidents-*",
        "behavior": "behavior-*",
        "frames": "frames-*",
        "calibration": "calibration",
    }

    def __init__(self, es_url: str, index_prefix: str = ""):
        """
        Initialize ES client.

        Args:
            es_url: Elasticsearch URL (e.g., "http://localhost:9200")
            index_prefix: Optional prefix for all indexes
        """
        self.client = AsyncElasticsearch([es_url])
        self.index_prefix = index_prefix

    async def close(self) -> None:
        """Close the Elasticsearch connection."""
        await self.client.close()

    async def __aenter__(self) -> "ESClient":
        """Async context manager entry."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object
    ) -> None:
        """Async context manager exit."""
        await self.close()

    def get_index(self, index_key: str) -> str:
        """
        Get full index name with prefix.

        Args:
            index_key: Key from INDEXES dict

        Returns:
            Full index name with prefix

        Raises:
            ValueError: If index_key is not in whitelist
        """
        if index_key not in self.INDEXES:
            raise ValueError(
                f"Video Analytics: Invalid index key '{index_key}', valid keys: {list(self.INDEXES.keys())}"
            )

        return f"{self.index_prefix}{self.INDEXES[index_key]}"

    async def search(
        self,
        index_key: str,
        query_body: dict,
        size: int = 100,
        sort: str | None = None,
        source_includes: list[str] | None = None,
        source_excludes: list[str] | None = None,
    ) -> list[dict]:
        """
        Search Elasticsearch and return matching documents.

        Similar to Elasticsearch.getSearchResults() in web-api-core

        Args:
            index_key: Index to search (from INDEXES whitelist)
            query_body: Elasticsearch query body
            size: Maximum number of results to return
            sort: Sort specification (e.g., "timestamp:desc")
            source_includes: List of fields to include in response (filters at ES level)
            source_excludes: List of fields to exclude from response

        Returns:
            List of document _source objects
        """
        index = self.get_index(index_key)

        # Check if index exists
        index_exists = await self.client.indices.exists(index=index)
        if not index_exists:
            return []

        # Create a copy of query_body to avoid modifying the original
        query_body_copy = deepcopy(query_body)

        # Add sort to query body
        # Parse "field:order" format into [{"field": {"order": "order"}}]
        if sort:
            if isinstance(sort, str) and ":" in sort:
                field, order = sort.split(":", 1)
                query_body_copy["sort"] = [{field: {"order": order}}]
            else:
                query_body_copy["sort"] = sort

        query_body_copy["size"] = size

        response = await self.client.search(
            index=index,
            body=query_body_copy,
            source_includes=source_includes if source_includes else None,
            source_excludes=source_excludes if source_excludes else None,
        )

        # Format results
        return [hit["_source"] for hit in response["hits"]["hits"]]

    async def aggregate(self, index_key: str, query_body: dict, aggs: dict) -> dict:
        """
        Run aggregation query and return results.

        Args:
            index_key: Index to search (from INDEXES whitelist)
            query_body: Elasticsearch query body (will be copied, not modified)
            aggs: Aggregation specification

        Returns:
            Aggregation results dictionary
        """
        index = self.get_index(index_key)

        # Check if index exists
        index_exists = await self.client.indices.exists(index=index)
        if not index_exists:
            return {}

        # Copy query body to avoid modifying the original
        query_with_aggs = deepcopy(query_body)
        query_with_aggs["aggs"] = aggs

        response = await self.client.search(
            index=index,
            body=query_with_aggs,
            size=0,  # Only want aggregations, not documents
        )

        return cast("dict[Any, Any]", response.get("aggregations", {}))

    async def get_by_id(self, index_key: str, doc_id: str) -> dict | None:
        """
        Get a single document by ID.

        Similar to getting calibration by ID in web-api-core

        Args:
            index_key: Index to search (from INDEXES whitelist)
            doc_id: Document ID

        Returns:
            Document _source or None if not found
        """
        index = self.get_index(index_key)

        # Check if index exists
        index_exists = await self.client.indices.exists(index=index)
        if not index_exists:
            return None

        query_body = {"query": {"ids": {"values": [doc_id]}}, "size": 1}

        response = await self.client.search(index=index, body=query_body)

        hits = response.get("hits", {}).get("hits", [])
        if hits:
            return cast("dict[Any, Any]", hits[0]["_source"])
        return None

    async def count(self, index_key: str, query_body: dict) -> int:
        """
        Count documents matching a query.

        Uses Elasticsearch count API which is efficient and doesn't have
        the 10,000 result window limit.

        Args:
            index_key: Index to search (from INDEXES whitelist)
            query_body: Elasticsearch query body

        Returns:
            Count of matching documents
        """
        index = self.get_index(index_key)

        # Check if index exists
        index_exists = await self.client.indices.exists(index=index)
        if not index_exists:
            return 0

        response = await self.client.count(index=index, body=query_body)

        return cast("int", response.get("count", 0))
