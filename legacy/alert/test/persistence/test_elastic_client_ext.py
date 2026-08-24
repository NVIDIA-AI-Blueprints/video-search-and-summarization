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

"""Unit tests for ElasticClient extensions added for the persistence layer."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from elastic.elastic import ElasticClient, ApiError, ConflictError


@pytest.fixture
def es_client():
    """Create an ElasticClient with a mocked Elasticsearch connection."""
    with patch("elastic.elastic.Elasticsearch") as mock_es_cls:
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_es_cls.return_value = mock_instance
        client = ElasticClient(url="http://localhost:9200")
        yield client


class _MockApiError(ApiError):
    """ApiError subclass that can be constructed without the real ES internals."""

    def __init__(self, status_code):
        self.meta = MagicMock()
        self.meta.status = status_code
        self.body = None
        self.message = f"mock {status_code}"
        Exception.__init__(self, self.message)


def _make_api_error(status_code):
    """Build a mock ApiError with the given HTTP status."""
    return _MockApiError(status_code)


class TestGetDocument:

    def test_get_existing_document(self, es_client):
        es_client.client.get.return_value = {
            "_id": "collision",
            "_source": {"alert_type": "collision", "prompt": "detect collisions"},
        }

        result = es_client.get_document("ab-alert-configs", "collision")

        assert result == {"alert_type": "collision", "prompt": "detect collisions"}
        es_client.client.get.assert_called_once_with(index="ab-alert-configs", id="collision")

    def test_get_nonexistent_returns_none(self, es_client):
        es_client.client.get.side_effect = _make_api_error(404)

        result = es_client.get_document("ab-alert-configs", "missing")

        assert result is None

    def test_get_server_error_raises(self, es_client):
        es_client.client.get.side_effect = _make_api_error(500)

        with pytest.raises(ApiError):
            es_client.get_document("ab-alert-configs", "collision")


class TestDeleteDocument:

    def test_delete_existing_returns_true(self, es_client):
        es_client.client.delete.return_value = {"result": "deleted"}

        result = es_client.delete_document("ab-alert-configs", "collision")

        assert result is True
        es_client.client.delete.assert_called_once_with(
            index="ab-alert-configs", id="collision", refresh="false"
        )

    def test_delete_with_refresh(self, es_client):
        es_client.client.delete.return_value = {"result": "deleted"}

        es_client.delete_document("ab-alert-configs", "collision", refresh="wait_for")

        es_client.client.delete.assert_called_once_with(
            index="ab-alert-configs", id="collision", refresh="wait_for"
        )

    def test_delete_nonexistent_returns_false(self, es_client):
        es_client.client.delete.side_effect = _make_api_error(404)

        result = es_client.delete_document("ab-alert-configs", "missing")

        assert result is False

    def test_delete_server_error_raises(self, es_client):
        es_client.client.delete.side_effect = _make_api_error(500)

        with pytest.raises(ApiError):
            es_client.delete_document("ab-alert-configs", "collision")


class TestSearchDocuments:

    def _mock_search_response(self, hits, total):
        return {
            "hits": {
                "total": {"value": total, "relation": "eq"},
                "hits": [
                    {"_id": h["id"], "_source": h["source"]} for h in hits
                ],
            }
        }

    def test_search_default_match_all(self, es_client):
        es_client.client.search.return_value = self._mock_search_response(
            [{"id": "a", "source": {"x": 1}}, {"id": "b", "source": {"x": 2}}],
            total=2,
        )

        result = es_client.search_documents("ab-alert-configs")

        assert result["total"] == 2
        assert len(result["hits"]) == 2
        assert result["hits"][0] == {"id": "a", "source": {"x": 1}}

        call_body = es_client.client.search.call_args
        assert call_body.kwargs["body"]["query"] == {"match_all": {}}

    def test_search_with_query_and_pagination(self, es_client):
        es_client.client.search.return_value = self._mock_search_response([], total=0)

        query = {"term": {"alert_type.keyword": "fire"}}
        es_client.search_documents("ab-alert-configs", query=query, size=10, from_=20)

        call_body = es_client.client.search.call_args.kwargs["body"]
        assert call_body["query"] == query
        assert call_body["size"] == 10
        assert call_body["from"] == 20

    def test_search_with_sort(self, es_client):
        es_client.client.search.return_value = self._mock_search_response([], total=0)

        sort = [{"created_at": "desc"}]
        es_client.search_documents("ab-alert-configs", sort=sort)

        call_body = es_client.client.search.call_args.kwargs["body"]
        assert call_body["sort"] == sort

    def test_search_index_not_found_returns_empty(self, es_client):
        es_client.client.search.side_effect = _make_api_error(404)

        result = es_client.search_documents("nonexistent-index")

        assert result == {"hits": [], "total": 0}

    def test_search_server_error_raises(self, es_client):
        es_client.client.search.side_effect = _make_api_error(500)

        with pytest.raises(ApiError):
            es_client.search_documents("ab-alert-configs")


class TestGetDocumentWithMeta:

    def test_returns_source_and_concurrency_metadata(self, es_client):
        es_client.client.get.return_value = {
            "_id": "collision",
            "_source": {"alert_type": "collision", "prompt": "x"},
            "_seq_no": 7,
            "_primary_term": 2,
        }

        result = es_client.get_document_with_meta("ab-alert-configs", "collision")

        assert result == {
            "source": {"alert_type": "collision", "prompt": "x"},
            "seq_no": 7,
            "primary_term": 2,
        }

    def test_returns_none_on_404(self, es_client):
        es_client.client.get.side_effect = _make_api_error(404)

        assert es_client.get_document_with_meta("ab-alert-configs", "missing") is None

    def test_server_error_raises(self, es_client):
        es_client.client.get.side_effect = _make_api_error(500)

        with pytest.raises(ApiError):
            es_client.get_document_with_meta("ab-alert-configs", "collision")


class TestWriteJsonOpType:

    def test_op_type_create_forwarded_to_client(self, es_client):
        es_client.client.index.return_value = {"result": "created"}

        es_client.write_json(
            "ab-alert-configs",
            {"alert_type": "collision"},
            doc_id="collision",
            op_type="create",
        )

        kwargs = es_client.client.index.call_args.kwargs
        assert kwargs["op_type"] == "create"
        assert kwargs["index"] == "ab-alert-configs"
        assert kwargs["id"] == "collision"

    def test_op_type_omitted_when_not_provided(self, es_client):
        es_client.client.index.return_value = {"result": "created"}

        es_client.write_json(
            "ab-alert-configs",
            {"alert_type": "collision"},
            doc_id="collision",
        )

        kwargs = es_client.client.index.call_args.kwargs
        assert "op_type" not in kwargs


class TestUpdateDocumentConcurrency:

    def test_if_seq_no_and_primary_term_forwarded(self, es_client):
        es_client.client.update.return_value = {"result": "updated"}

        es_client.update_document(
            "ab-alert-configs",
            "collision",
            {"prompt": "new"},
            if_seq_no=42,
            if_primary_term=3,
        )

        kwargs = es_client.client.update.call_args.kwargs
        assert kwargs["if_seq_no"] == 42
        assert kwargs["if_primary_term"] == 3

    def test_concurrency_params_omitted_when_not_provided(self, es_client):
        es_client.client.update.return_value = {"result": "updated"}

        es_client.update_document(
            "ab-alert-configs",
            "collision",
            {"prompt": "new"},
        )

        kwargs = es_client.client.update.call_args.kwargs
        assert "if_seq_no" not in kwargs
        assert "if_primary_term" not in kwargs

    def test_concurrency_params_rejected_when_only_one_provided(self, es_client):
        """Partial OCC would silently degrade to an unconditional update
        and drop the concurrency guarantee the caller expects. The
        client must reject the inconsistent combination up front."""
        with pytest.raises(ValueError, match="if_seq_no and if_primary_term"):
            es_client.update_document(
                "ab-alert-configs",
                "collision",
                {"prompt": "new"},
                if_seq_no=42,
            )
        with pytest.raises(ValueError, match="if_seq_no and if_primary_term"):
            es_client.update_document(
                "ab-alert-configs",
                "collision",
                {"prompt": "new"},
                if_primary_term=3,
            )
        es_client.client.update.assert_not_called()
