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

"""Unit tests for ElasticPersistenceStore."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from elastic.elastic import ConflictError
from persistence.elastic_store import ElasticPersistenceStore
from persistence.exceptions import (
    ConcurrentModificationError,
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    PersistenceError,
)


def _make_conflict_error():
    """Build a mock ConflictError without invoking ApiError internals."""
    err = ConflictError.__new__(ConflictError)
    err.meta = MagicMock()
    err.meta.status = 409
    err.body = None
    err.message = "version_conflict"
    Exception.__init__(err, "version_conflict")
    return err


@pytest.fixture
def mock_es_client():
    client = MagicMock()
    client.ping.return_value = True
    client.ensure_json_index.return_value = None
    return client


@pytest.fixture
def store(mock_es_client):
    return ElasticPersistenceStore(mock_es_client, index_prefix="ab-")


class TestCreate:

    def test_create_new_document(self, store, mock_es_client):
        mock_es_client.write_json.return_value = {"result": "created"}

        doc = {"alert_type": "collision", "prompt": "detect collisions"}
        result = store.create("alert-configs", "collision", doc)

        assert result["alert_type"] == "collision"
        assert result["prompt"] == "detect collisions"
        assert "created_at" in result
        assert "updated_at" in result

        mock_es_client.ensure_json_index.assert_called_once_with(
            "ab-alert-configs", shards=1, replicas=0,
        )
        mock_es_client.write_json.assert_called_once()
        call_kwargs = mock_es_client.write_json.call_args
        assert call_kwargs.kwargs["doc_id"] == "collision"
        assert call_kwargs.kwargs["refresh"] == "wait_for"
        assert call_kwargs.kwargs["op_type"] == "create"

    def test_create_duplicate_raises(self, store, mock_es_client):
        mock_es_client.write_json.side_effect = _make_conflict_error()

        with pytest.raises(DocumentAlreadyExistsError) as exc_info:
            store.create("alert-configs", "collision", {"prompt": "x"})

        assert exc_info.value.collection == "alert-configs"
        assert exc_info.value.doc_id == "collision"

    def test_create_preserves_caller_created_at(self, store, mock_es_client):
        mock_es_client.write_json.return_value = {"result": "created"}

        caller_ts = "2026-01-01T00:00:00Z"
        doc = {"prompt": "test", "created_at": caller_ts}
        result = store.create("alert-configs", "test-id", doc)

        assert result["created_at"] == caller_ts

    def test_create_es_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.write_json.side_effect = Exception("connection refused")

        with pytest.raises(PersistenceError):
            store.create("alert-configs", "collision", {"prompt": "x"})

    def test_create_honours_auto_create_indices_false(self, mock_es_client):
        # Ops path: indices are pre-created via ES templates, so the store
        # must not hit ensure_json_index on the write path at all.
        mock_es_client.write_json.return_value = {"result": "created"}
        store = ElasticPersistenceStore(
            mock_es_client, auto_create_indices=False,
        )
        store.create("alert-configs", "collision", {"alert_type": "collision"})

        mock_es_client.ensure_json_index.assert_not_called()
        mock_es_client.write_json.assert_called_once()

    def test_create_honours_custom_shards_and_replicas(self, mock_es_client):
        mock_es_client.write_json.return_value = {"result": "created"}
        store = ElasticPersistenceStore(
            mock_es_client, index_shards=3, index_replicas=2,
        )
        store.create("alert-configs", "collision", {"alert_type": "collision"})

        mock_es_client.ensure_json_index.assert_called_once_with(
            "ab-alert-configs", shards=3, replicas=2,
        )

    def test_create_index_creation_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.ensure_json_index.side_effect = Exception("permission denied")

        with pytest.raises(PersistenceError):
            store.create("alert-configs", "collision", {"prompt": "x"})

        mock_es_client.write_json.assert_not_called()


class TestRead:

    def test_read_existing(self, store, mock_es_client):
        mock_es_client.get_document.return_value = {
            "alert_type": "collision",
            "prompt": "detect collisions",
        }

        result = store.read("alert-configs", "collision")

        assert result["alert_type"] == "collision"
        assert result["_id"] == "collision"
        mock_es_client.get_document.assert_called_once_with("ab-alert-configs", "collision")

    def test_read_nonexistent_raises(self, store, mock_es_client):
        mock_es_client.get_document.return_value = None

        with pytest.raises(DocumentNotFoundError) as exc_info:
            store.read("alert-configs", "missing")

        assert exc_info.value.doc_id == "missing"

    def test_read_es_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.get_document.side_effect = Exception("timeout")

        with pytest.raises(PersistenceError):
            store.read("alert-configs", "collision")


class TestUpdate:

    def test_update_merges_partial(self, store, mock_es_client):
        mock_es_client.get_document_with_meta.return_value = {
            "source": {
                "alert_type": "collision",
                "prompt": "old prompt",
                "created_at": "2026-01-01T00:00:00Z",
            },
            "seq_no": 5,
            "primary_term": 1,
        }
        mock_es_client.update_document.return_value = {"result": "updated"}

        result = store.update("alert-configs", "collision", {"prompt": "new prompt"})

        assert result["prompt"] == "new prompt"
        assert result["alert_type"] == "collision"
        assert result["created_at"] == "2026-01-01T00:00:00Z"
        assert result["_id"] == "collision"
        assert "updated_at" in result

        call_kwargs = mock_es_client.update_document.call_args.kwargs
        partial_sent = call_kwargs.get("partial_doc") or mock_es_client.update_document.call_args.args[2]
        assert partial_sent["prompt"] == "new prompt"
        assert "updated_at" in partial_sent
        assert call_kwargs["if_seq_no"] == 5
        assert call_kwargs["if_primary_term"] == 1

    def test_update_nonexistent_raises(self, store, mock_es_client):
        mock_es_client.get_document_with_meta.return_value = None

        with pytest.raises(DocumentNotFoundError):
            store.update("alert-configs", "missing", {"prompt": "x"})

    def test_update_es_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"prompt": "old"}, "seq_no": 1, "primary_term": 1,
        }
        mock_es_client.update_document.side_effect = Exception("write error")

        with pytest.raises(PersistenceError):
            store.update("alert-configs", "collision", {"prompt": "new"})

    def test_update_meta_read_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.get_document_with_meta.side_effect = Exception("ES timeout")

        with pytest.raises(PersistenceError):
            store.update("alert-configs", "collision", {"prompt": "new"})

        mock_es_client.update_document.assert_not_called()

    def test_update_retries_on_version_conflict(self, store, mock_es_client):
        # First two calls conflict, third succeeds.
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"prompt": "old"}, "seq_no": 1, "primary_term": 1,
        }
        mock_es_client.update_document.side_effect = [
            _make_conflict_error(),
            _make_conflict_error(),
            {"result": "updated"},
        ]

        result = store.update("alert-configs", "collision", {"prompt": "new"})

        assert result["prompt"] == "new"
        assert mock_es_client.update_document.call_count == 3

    def test_update_raises_after_max_retries(self, mock_es_client):
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"prompt": "old"}, "seq_no": 1, "primary_term": 1,
        }
        mock_es_client.update_document.side_effect = _make_conflict_error()

        store = ElasticPersistenceStore(mock_es_client, index_prefix="ab-", update_retries=2)

        with pytest.raises(ConcurrentModificationError) as exc_info:
            store.update("alert-configs", "collision", {"prompt": "new"})

        assert exc_info.value.retries == 2
        assert mock_es_client.update_document.call_count == 3

    def test_update_refuses_when_seq_no_missing(self, store, mock_es_client):
        # Missing OCC metadata would silently turn update_document into an
        # unconditional write. The store must refuse, not quietly degrade.
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"prompt": "old"}, "seq_no": None, "primary_term": 1,
        }

        with pytest.raises(PersistenceError, match="_seq_no/_primary_term"):
            store.update("alert-configs", "collision", {"prompt": "new"})

        mock_es_client.update_document.assert_not_called()

    def test_update_refuses_when_primary_term_missing(self, store, mock_es_client):
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"prompt": "old"}, "seq_no": 5, "primary_term": None,
        }

        with pytest.raises(PersistenceError, match="_seq_no/_primary_term"):
            store.update("alert-configs", "collision", {"prompt": "new"})

        mock_es_client.update_document.assert_not_called()


class TestDelete:

    def test_delete_existing(self, store, mock_es_client):
        mock_es_client.delete_document.return_value = True

        result = store.delete("alert-configs", "collision")

        assert result is True
        mock_es_client.delete_document.assert_called_once_with(
            "ab-alert-configs", "collision", refresh="wait_for"
        )

    def test_delete_nonexistent_raises(self, store, mock_es_client):
        mock_es_client.delete_document.return_value = False

        with pytest.raises(DocumentNotFoundError):
            store.delete("alert-configs", "missing")

    def test_delete_es_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.delete_document.side_effect = Exception("connection lost")

        with pytest.raises(PersistenceError):
            store.delete("alert-configs", "collision")


class TestList:

    def test_list_all_no_filters(self, store, mock_es_client):
        mock_es_client.search_documents.return_value = {
            "hits": [
                {"id": "collision", "source": {"alert_type": "collision"}},
                {"id": "fire", "source": {"alert_type": "fire"}},
            ],
            "total": 2,
        }

        result = store.list("alert-configs")

        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["alert_type"] == "collision"
        # Every item must carry its ES _id so callers can correlate to
        # subsequent update/delete calls without mirroring doc_id into
        # the document body.
        assert result["items"][0]["_id"] == "collision"
        assert result["items"][1]["_id"] == "fire"

    def test_list_with_string_filter(self, store, mock_es_client):
        mock_es_client.search_documents.return_value = {
            "hits": [{"id": "collision", "source": {"alert_type": "collision"}}],
            "total": 1,
        }

        store.list("alert-configs", filters={"alert_type": "collision"})

        call_kwargs = mock_es_client.search_documents.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        assert query == {"bool": {"must": [{"term": {"alert_type.keyword": "collision"}}]}}

    def test_list_with_numeric_filter(self, store, mock_es_client):
        mock_es_client.search_documents.return_value = {"hits": [], "total": 0}

        store.list("alert-configs", filters={"priority": 1})

        call_kwargs = mock_es_client.search_documents.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        assert query == {"bool": {"must": [{"term": {"priority": 1}}]}}

    def test_list_empty_collection(self, store, mock_es_client):
        mock_es_client.search_documents.return_value = {"hits": [], "total": 0}

        result = store.list("alert-configs")

        assert result == {"items": [], "total": 0}

    def test_list_es_failure_raises_persistence_error(self, store, mock_es_client):
        mock_es_client.search_documents.side_effect = Exception("search failed")

        with pytest.raises(PersistenceError):
            store.list("alert-configs")


class TestExists:

    def test_exists_true(self, store, mock_es_client):
        mock_es_client.get_document.return_value = {"alert_type": "collision"}

        assert store.exists("alert-configs", "collision") is True

    def test_exists_false(self, store, mock_es_client):
        mock_es_client.get_document.return_value = None

        assert store.exists("alert-configs", "missing") is False

    def test_exists_propagates_backend_errors(self, store, mock_es_client):
        # Backend outage must surface as PersistenceError — callers that
        # use exists() for control flow cannot mistake "ES is down" for
        # "document is absent".
        mock_es_client.get_document.side_effect = ConnectionError("ES down")

        with pytest.raises(PersistenceError):
            store.exists("alert-configs", "collision")


class TestHealth:

    def test_health_delegates_to_ping(self, store, mock_es_client):
        mock_es_client.ping.return_value = True
        assert store.health() is True

        mock_es_client.ping.return_value = False
        assert store.health() is False


class TestRoundTripMetadataStripping:
    """The read paths echo ES metadata (``_id`` etc.) into the
    returned doc so callers can correlate ids and concurrency tokens.
    The write paths must strip those fields back out — ES 8.x rejects
    any underscore-prefixed metadata field inside the document body
    with ``document_parsing_exception``. Anyone who reads, mutates,
    and writes back the same dict (e.g. ``AlertConfigService.update``,
    or the startup ``save_to_redis`` seed path) would otherwise round-
    trip the injected ``_id`` straight into ES and fail loudly."""

    def test_create_strips_underscore_metadata_from_input(self, store, mock_es_client):
        # Caller passed back a previously-read doc complete with the
        # ``_id`` echoed by ``read``. ES would reject that body — the
        # store must strip it before forwarding. The mutable dict is
        # snapshotted at call time because ``create`` re-injects
        # ``_id`` on the return path for caller convenience.
        captured: Dict[str, Any] = {}

        def snapshot_doc(index, doc, **kwargs):
            captured.update(doc)

        mock_es_client.write_json.side_effect = snapshot_doc

        store.create(
            "alert-configs",
            "collision",
            {
                "alert_type": "collision",
                "prompt": "p",
                "_id": "collision",
                "_seq_no": 5,
                "_primary_term": 1,
                "_index": "ab-alert-configs",
                "_version": 3,
            },
        )

        for forbidden in ("_id", "_seq_no", "_primary_term", "_index", "_version"):
            assert forbidden not in captured, (
                f"{forbidden} must be stripped before reaching ES "
                f"(ES 8.x rejects metadata fields in the document body)"
            )
        # Non-metadata content is preserved verbatim.
        assert captured["alert_type"] == "collision"
        assert captured["prompt"] == "p"

    def test_update_strips_underscore_metadata_from_partial(self, store, mock_es_client):
        # The classic round-trip: read returns a dict with ``_id``,
        # caller mutates one field and passes the dict back to update.
        mock_es_client.get_document_with_meta.return_value = {
            "source": {"alert_type": "collision", "prompt": "old"},
            "seq_no": 5,
            "primary_term": 1,
        }
        mock_es_client.update_document.return_value = {"result": "updated"}

        store.update(
            "alert-configs",
            "collision",
            {
                "alert_type": "collision",
                "prompt": "new",
                "_id": "collision",
                "_seq_no": 5,
                "_primary_term": 1,
            },
        )

        partial_sent = mock_es_client.update_document.call_args.args[2]
        for forbidden in ("_id", "_seq_no", "_primary_term"):
            assert forbidden not in partial_sent, (
                f"{forbidden} must be stripped before reaching ES "
                f"update_document (round-trip read → write would "
                f"otherwise surface BadRequestError 400)"
            )
        assert partial_sent["prompt"] == "new"
        assert "updated_at" in partial_sent
