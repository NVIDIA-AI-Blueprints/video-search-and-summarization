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

"""Unit tests for IncidentService."""

from unittest.mock import MagicMock

import pytest

from realtime.config import ErrorCode, ResponseStatus
from realtime.services.incident_service import IncidentService


# ---------------------------------------------------------------------------
# list_incidents — happy path
# ---------------------------------------------------------------------------

class TestListIncidentsSuccess:
    """IncidentService.list_incidents — success paths."""

    @pytest.mark.asyncio
    async def test_returns_200_with_hits(self, incident_service, mock_es_client):
        data, code = await incident_service.list_incidents()

        assert code == 200
        assert data["status"] == ResponseStatus.SUCCESS
        assert data["count"] == 2
        assert data["total"] == 2
        assert len(data["incidents"]) == 2

    @pytest.mark.asyncio
    async def test_each_incident_has_meta(self, incident_service):
        data, _ = await incident_service.list_incidents()

        for inc in data["incidents"]:
            assert "_id" in inc
            assert "_index" in inc

    @pytest.mark.asyncio
    async def test_sensor_id_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(sensor_id="cam-1")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        assert any(c.get("term", {}).get("sensorId.keyword") == "cam-1" for c in must)

    @pytest.mark.asyncio
    async def test_sensor_id_filter_uses_keyword_for_hyphenated_ids(
        self,
        incident_service,
        mock_es_client,
    ):
        await incident_service.list_incidents(sensor_id="realtime-source")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        terms = [c.get("term", {}) for c in must]
        assert {"sensorId.keyword": "realtime-source"} in terms
        assert {"sensorId": "realtime-source"} not in terms

    @pytest.mark.asyncio
    async def test_category_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(category="fire")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        assert any(c.get("term", {}).get("category.keyword") == "fire" for c in must)

    @pytest.mark.asyncio
    async def test_time_range_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-02T00:00:00Z",
        )

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        range_clauses = [c for c in must if "range" in c]
        assert len(range_clauses) == 1
        ts = range_clauses[0]["range"]["timestamp"]
        assert ts["gte"] == "2025-01-01T00:00:00Z"
        assert ts["lte"] == "2025-01-02T00:00:00Z"

    @pytest.mark.asyncio
    async def test_no_filters_uses_match_all(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        assert "match_all" in query

    @pytest.mark.asyncio
    async def test_pagination_params_forwarded(self, incident_service, mock_es_client):
        await incident_service.list_incidents(limit=25, offset=50)

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["size"] == 25
        assert call_kwargs["from_"] == 50

    @pytest.mark.asyncio
    async def test_index_pattern(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["index"] == "mdx-vlm-incidents-*"

    @pytest.mark.asyncio
    async def test_sort_descending_timestamp(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["sort"] == [{"timestamp": {"order": "desc"}}]


# ---------------------------------------------------------------------------
# list_incidents — failure paths
# ---------------------------------------------------------------------------

class TestListIncidentsFailure:
    """IncidentService.list_incidents — error handling."""

    @pytest.mark.asyncio
    async def test_no_es_client_returns_503(self):
        svc = IncidentService(es_client=None)
        data, code = await svc.list_incidents()

        assert code == 503
        assert data["error"] == ErrorCode.ELASTICSEARCH_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_es_search_exception_returns_500(self, mock_es_client):
        mock_es_client.client.search.side_effect = Exception("cluster timeout")
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 500
        assert data["error"] == ErrorCode.ELASTICSEARCH_QUERY_FAILED
        assert "cluster timeout" in data["message"]

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_es_client):
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 0}, "hits": []}
        }
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 200
        assert data["count"] == 0
        assert data["total"] == 0
        assert data["incidents"] == []

    @pytest.mark.asyncio
    async def test_total_as_int(self, mock_es_client):
        """ES 6.x returns total as int, not dict."""
        mock_es_client.client.search.return_value = {
            "hits": {"total": 42, "hits": []}
        }
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 200
        assert data["total"] == 42


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestIncidentServiceInit:
    """IncidentService initialization."""

    def test_with_injected_client(self, mock_es_client):
        svc = IncidentService(es_client=mock_es_client, index_base="custom-index")
        assert svc._es_client is mock_es_client
        assert svc._index_base == "custom-index"

    def test_without_client(self):
        svc = IncidentService()
        assert svc._es_client is None

    def test_custom_index_base(self):
        svc = IncidentService(index_base="my-incidents")
        assert svc._index_base == "my-incidents"
