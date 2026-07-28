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

"""Unit tests for ``clients.elastic``.

``ElasticClient`` is the indexing path for every enriched event. Three
behaviours here decide whether data lands correctly or is silently lost:

* **404 is not an error.** ``get`` / ``delete`` / ``search`` /
  ``delete_by_query`` translate a 404 into ``None`` / ``False`` / an empty
  result, while every other ``ApiError`` propagates. Conflating the two would
  either spam errors for absent documents or swallow a real cluster failure.
* **Optimistic concurrency is all-or-nothing.** ``update_document`` rejects
  ``if_seq_no`` without ``if_primary_term`` up front, because ES would
  silently degrade a guarded update to an unconditional one.
* **``write_event_response`` orders its steps deliberately.** The fingerprint
  (the ES document id, and therefore the dedup key) is computed *before* the
  category mapping and verdict-description override are applied, so renaming
  a category for display cannot change the document id.

``Elasticsearch`` is patched at the module boundary; the constructor's
connection probe is satisfied by a mocked ``ping``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from elasticsearch import ApiError

from clients.elastic import ElasticClient, ElasticConfig


def api_error(status):
    """Build an ``ApiError`` whose ``meta.status`` is ``status``."""
    meta = MagicMock()
    meta.status = status
    return ApiError("boom", meta=meta, body={})


def make_client(url="http://es:9200", config=None, ping=True):
    with patch("clients.elastic.Elasticsearch") as es_cls:
        es_cls.return_value.ping.return_value = ping
        return ElasticClient(url=url, config=config)


@pytest.fixture
def client():
    return make_client()


class TestElasticConfig:
    def test_defaults(self):
        config = ElasticConfig()
        assert config.hosts == tuple()
        assert config.verify_certs is False
        assert config.request_timeout == 10
        assert config.api_key is None


class TestConstruction:
    def test_url_becomes_the_single_host(self):
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(url="http://es:9200")

        assert es_cls.call_args.kwargs["hosts"] == ["http://es:9200"]

    def test_url_overrides_configured_hosts(self):
        config = ElasticConfig(hosts=("http://other:9200",))
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(url="http://es:9200", config=config)

        assert es_cls.call_args.kwargs["hosts"] == ["http://es:9200"]

    def test_config_hosts_are_used_without_a_url(self):
        config = ElasticConfig(hosts=("http://es:9200",))
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["hosts"] == ["http://es:9200"]

    def test_no_host_and_no_cloud_id_raises(self):
        with pytest.raises(ValueError, match="requires a 'hosts' entry or cloud_id"):
            ElasticClient()

    def test_cloud_id_replaces_hosts(self):
        config = ElasticConfig(cloud_id="deployment:abc")
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["cloud_id"] == "deployment:abc"
        assert "hosts" not in es_cls.call_args.kwargs

    def test_api_key_wins_over_basic_auth(self):
        config = ElasticConfig(hosts=("http://es:9200",), api_key="k", username="u", password="p")
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["api_key"] == "k"
        assert "basic_auth" not in es_cls.call_args.kwargs

    def test_username_and_password_become_basic_auth(self):
        config = ElasticConfig(hosts=("http://es:9200",), username="u", password="p")
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["basic_auth"] == ("u", "p")

    def test_username_without_password_is_ignored(self):
        config = ElasticConfig(hosts=("http://es:9200",), username="u")
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert "basic_auth" not in es_cls.call_args.kwargs

    def test_ca_certs_are_forwarded(self):
        config = ElasticConfig(hosts=("http://es:9200",), ca_certs="/certs/ca.pem")
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["ca_certs"] == "/certs/ca.pem"

    def test_verify_certs_and_timeout_are_forwarded(self):
        config = ElasticConfig(hosts=("http://es:9200",), verify_certs=True, request_timeout=45)
        with patch("clients.elastic.Elasticsearch") as es_cls:
            es_cls.return_value.ping.return_value = True
            ElasticClient(config=config)

        assert es_cls.call_args.kwargs["verify_certs"] is True
        assert es_cls.call_args.kwargs["request_timeout"] == 45

    def test_unreachable_cluster_raises_at_construction(self):
        with pytest.raises(ConnectionError, match="Failed to connect to Elasticsearch"):
            make_client(ping=False)

    def test_index_cache_starts_empty(self, client):
        assert client._index_cache == set()


class TestPing:
    def test_true_when_the_cluster_answers(self, client):
        client.client.ping.return_value = True
        assert client.ping() is True

    def test_false_when_the_cluster_says_no(self, client):
        client.client.ping.return_value = False
        assert client.ping() is False

    def test_transport_error_is_reported_as_unreachable(self, client):
        client.client.ping.side_effect = RuntimeError("connection reset")
        assert client.ping() is False


class TestGenerateDailyIndexName:
    def test_z_suffixed_timestamp(self, client):
        assert client.generate_daily_index_name(
            "mdx-vlm-incidents", "2025-09-19T08:23:06.870Z"
        ) == "mdx-vlm-incidents-2025-09-19"

    def test_offset_timestamp(self, client):
        assert client.generate_daily_index_name(
            "idx", "2025-09-19T08:23:06+02:00"
        ) == "idx-2025-09-19"

    def test_naive_timestamp(self, client):
        assert client.generate_daily_index_name("idx", "2025-09-19T08:23:06") == "idx-2025-09-19"

    @pytest.mark.parametrize("bad", ["not-a-timestamp", ""])
    def test_unparseable_timestamp_falls_back_to_today(self, client, bad):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert client.generate_daily_index_name("idx", bad) == f"idx-{today}"


class TestWriteJson:
    def test_indexes_the_document(self, client):
        client.client.index.return_value = {"result": "created"}

        result = client.write_json("idx", {"a": 1}, doc_id="d-1")

        kwargs = client.client.index.call_args.kwargs
        assert kwargs["index"] == "idx"
        assert kwargs["id"] == "d-1"
        assert kwargs["document"] == {"a": 1}
        assert kwargs["refresh"] == "false"
        assert result == {"result": "created"}

    def test_the_caller_document_is_not_mutated(self, client):
        client.client.index.return_value = {}
        document = {"a": 1}

        client.write_json("idx", document)

        assert client.client.index.call_args.kwargs["document"] is not document

    def test_op_type_is_forwarded_when_set(self, client):
        client.client.index.return_value = {}
        client.write_json("idx", {}, doc_id="d-1", op_type="create")

        assert client.client.index.call_args.kwargs["op_type"] == "create"

    def test_op_type_is_omitted_by_default(self, client):
        client.client.index.return_value = {}
        client.write_json("idx", {})

        assert "op_type" not in client.client.index.call_args.kwargs

    def test_custom_refresh_is_forwarded(self, client):
        client.client.index.return_value = {}
        client.write_json("idx", {}, refresh="wait_for")

        assert client.client.index.call_args.kwargs["refresh"] == "wait_for"

    def test_non_dict_result_is_tolerated(self, client):
        client.client.index.return_value = "ok"
        assert client.write_json("idx", {}) == "ok"

    def test_index_failure_propagates(self, client):
        client.client.index.side_effect = RuntimeError("cluster down")

        with pytest.raises(RuntimeError, match="cluster down"):
            client.write_json("idx", {})


class TestUpdateDocument:
    def test_partial_update(self, client):
        client.client.update.return_value = {"result": "updated"}

        result = client.update_document("idx", "d-1", {"b": 2})

        kwargs = client.client.update.call_args.kwargs
        assert kwargs["doc"] == {"b": 2}
        assert "if_seq_no" not in kwargs
        assert result == {"result": "updated"}

    def test_optimistic_concurrency_fields_are_forwarded_together(self, client):
        client.client.update.return_value = {}

        client.update_document("idx", "d-1", {"b": 2}, if_seq_no=5, if_primary_term=1)

        kwargs = client.client.update.call_args.kwargs
        assert kwargs["if_seq_no"] == 5
        assert kwargs["if_primary_term"] == 1

    @pytest.mark.parametrize(
        "kwargs", [{"if_seq_no": 5}, {"if_primary_term": 1}]
    )
    def test_half_specified_concurrency_is_rejected(self, client, kwargs):
        """ES would silently degrade to an unconditional update."""
        with pytest.raises(ValueError, match="must both be provided together"):
            client.update_document("idx", "d-1", {"b": 2}, **kwargs)

        client.client.update.assert_not_called()

    def test_seq_no_zero_is_treated_as_supplied(self, client):
        client.client.update.return_value = {}
        client.update_document("idx", "d-1", {}, if_seq_no=0, if_primary_term=1)

        assert client.client.update.call_args.kwargs["if_seq_no"] == 0

    def test_update_failure_propagates(self, client):
        client.client.update.side_effect = RuntimeError("conflict")

        with pytest.raises(RuntimeError, match="conflict"):
            client.update_document("idx", "d-1", {})


class TestGetDocument:
    def test_returns_the_source(self, client):
        client.client.get.return_value = {"_source": {"a": 1}}
        assert client.get_document("idx", "d-1") == {"a": 1}

    def test_missing_document_returns_none(self, client):
        client.client.get.side_effect = api_error(404)
        assert client.get_document("idx", "d-1") is None

    def test_other_api_errors_propagate(self, client):
        client.client.get.side_effect = api_error(503)

        with pytest.raises(ApiError):
            client.get_document("idx", "d-1")


class TestGetDocumentWithMeta:
    def test_returns_source_and_concurrency_metadata(self, client):
        client.client.get.return_value = {"_source": {"a": 1}, "_seq_no": 5, "_primary_term": 1}

        assert client.get_document_with_meta("idx", "d-1") == {
            "source": {"a": 1},
            "seq_no": 5,
            "primary_term": 1,
        }

    def test_absent_metadata_becomes_none(self, client):
        client.client.get.return_value = {"_source": {"a": 1}}
        result = client.get_document_with_meta("idx", "d-1")

        assert result["seq_no"] is None
        assert result["primary_term"] is None

    def test_missing_document_returns_none(self, client):
        client.client.get.side_effect = api_error(404)
        assert client.get_document_with_meta("idx", "d-1") is None

    def test_other_api_errors_propagate(self, client):
        client.client.get.side_effect = api_error(500)

        with pytest.raises(ApiError):
            client.get_document_with_meta("idx", "d-1")


class TestDeleteDocument:
    def test_true_when_deleted(self, client):
        client.client.delete.return_value = {"result": "deleted"}
        assert client.delete_document("idx", "d-1") is True

    def test_false_when_absent(self, client):
        client.client.delete.side_effect = api_error(404)
        assert client.delete_document("idx", "d-1") is False

    def test_refresh_is_forwarded(self, client):
        client.client.delete.return_value = {}
        client.delete_document("idx", "d-1", refresh="true")

        assert client.client.delete.call_args.kwargs["refresh"] == "true"

    def test_other_api_errors_propagate(self, client):
        client.client.delete.side_effect = api_error(503)

        with pytest.raises(ApiError):
            client.delete_document("idx", "d-1")


class TestSearchDocuments:
    def test_flattens_hits(self, client):
        client.client.search.return_value = {
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {"_id": "d-1", "_source": {"a": 1}},
                    {"_id": "d-2", "_source": {"a": 2}},
                ],
            }
        }

        result = client.search_documents("idx")

        assert result["total"] == 2
        assert result["hits"] == [
            {"id": "d-1", "source": {"a": 1}},
            {"id": "d-2", "source": {"a": 2}},
        ]

    def test_defaults_to_match_all(self, client):
        client.client.search.return_value = {"hits": {}}
        client.search_documents("idx")

        assert client.client.search.call_args.kwargs["body"]["query"] == {"match_all": {}}

    def test_pagination_and_sort_are_forwarded(self, client):
        client.client.search.return_value = {"hits": {}}
        client.search_documents(
            "idx", query={"term": {"a": 1}}, size=10, from_=20, sort=[{"created_at": "desc"}]
        )

        body = client.client.search.call_args.kwargs["body"]
        assert body["size"] == 10
        assert body["from"] == 20
        assert body["sort"] == [{"created_at": "desc"}]
        assert body["query"] == {"term": {"a": 1}}

    def test_sort_is_omitted_when_not_requested(self, client):
        client.client.search.return_value = {"hits": {}}
        client.search_documents("idx")

        assert "sort" not in client.client.search.call_args.kwargs["body"]

    def test_scalar_total_is_supported(self, client):
        """Older ES versions return a bare integer total."""
        client.client.search.return_value = {"hits": {"total": 7, "hits": []}}
        assert client.search_documents("idx")["total"] == 7

    def test_missing_index_returns_an_empty_result(self, client):
        client.client.search.side_effect = api_error(404)
        assert client.search_documents("idx") == {"hits": [], "total": 0}

    def test_other_api_errors_propagate(self, client):
        client.client.search.side_effect = api_error(503)

        with pytest.raises(ApiError):
            client.search_documents("idx")


class TestDeleteByQuery:
    def test_forwards_the_throttling_options(self, client):
        client.client.delete_by_query.return_value = {"deleted": 3}

        result = client.delete_by_query(
            "idx", {"term": {"a": 1}}, requests_per_second=2.5, slices=4, refresh=True
        )

        kwargs = client.client.delete_by_query.call_args.kwargs
        assert kwargs["requests_per_second"] == 2.5
        assert kwargs["slices"] == 4
        assert kwargs["refresh"] is True
        assert kwargs["conflicts"] == "proceed"
        assert result == {"deleted": 3}

    def test_unthrottled_by_default(self, client):
        client.client.delete_by_query.return_value = {}
        client.delete_by_query("idx", {})

        kwargs = client.client.delete_by_query.call_args.kwargs
        assert "requests_per_second" not in kwargs
        assert kwargs["slices"] == "auto"

    def test_non_dict_result_degrades_to_zero_deleted(self, client):
        client.client.delete_by_query.return_value = "ok"
        assert client.delete_by_query("idx", {}) == {"deleted": 0}

    def test_missing_index_reports_zero_deleted(self, client):
        client.client.delete_by_query.side_effect = api_error(404)
        assert client.delete_by_query("idx", {}) == {"deleted": 0}

    def test_other_api_errors_propagate(self, client):
        client.client.delete_by_query.side_effect = api_error(503)

        with pytest.raises(ApiError):
            client.delete_by_query("idx", {})


class TestEnsureJsonIndex:
    def test_creates_a_missing_index(self, client):
        client.client.indices.exists.return_value = False

        client.ensure_json_index("idx", shards=2, replicas=1)

        kwargs = client.client.indices.create.call_args.kwargs
        assert kwargs["index"] == "idx"
        assert kwargs["settings"] == {"number_of_shards": 2, "number_of_replicas": 1}
        assert kwargs["mappings"] == {"dynamic": True}

    def test_existing_index_is_not_recreated(self, client):
        client.client.indices.exists.return_value = True

        client.ensure_json_index("idx")

        client.client.indices.create.assert_not_called()
        assert "idx" in client._index_cache

    def test_the_result_is_cached(self, client):
        client.client.indices.exists.return_value = True

        client.ensure_json_index("idx")
        client.ensure_json_index("idx")

        assert client.client.indices.exists.call_count == 1

    def test_a_creation_race_is_absorbed(self, client):
        """A concurrent writer may have created the index between the checks."""
        client.client.indices.exists.return_value = False
        client.client.indices.create.side_effect = api_error(400)

        client.ensure_json_index("idx")

        assert "idx" in client._index_cache

    def test_other_api_errors_propagate_and_are_not_cached(self, client):
        client.client.indices.exists.side_effect = api_error(503)

        with pytest.raises(ApiError):
            client.ensure_json_index("idx")

        assert "idx" not in client._index_cache


class TestWriteEventResponse:
    INCIDENT = {
        "sensorId": "cam-1",
        "category": "collision",
        "timestamp": "2025-09-19T08:23:06.870Z",
        "info": {"verdict": "yes"},
    }

    @pytest.fixture
    def client(self):
        client = make_client()
        client.client.indices.exists.return_value = True
        client.client.index.return_value = {"result": "created"}
        return client

    def test_writes_to_the_daily_index(self, client):
        client.write_event_response(dict(self.INCIDENT), {}, "prompt", "mdx-vlm-incidents")

        assert client.client.index.call_args.kwargs["index"] == "mdx-vlm-incidents-2025-09-19"

    def test_document_id_is_the_fingerprint(self, client):
        client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")

        kwargs = client.client.index.call_args.kwargs
        assert kwargs["id"]
        assert kwargs["document"]["Id"] == kwargs["id"]

    def test_the_caller_message_is_not_mutated(self, client):
        message = dict(self.INCIDENT)
        client.write_event_response(message, {}, "p", "idx")

        assert "Id" not in message

    def test_alert_documents_use_the_alert_fingerprint_scheme(self, client):
        """Alerts and incidents fingerprint over different field sets."""
        alert = {
            "notification_type": "alert",
            "sensorId": "cam-1",
            "sensor": {"id": "cam-1"},
            "category": "collision",
            "analyticsModule": {"id": "intrusion"},
            "timestamp": "2025-09-19T08:23:06.870Z",
        }
        client.write_event_response(alert, {}, "p", "idx")
        alert_id = client.client.index.call_args.kwargs["id"]

        client.write_event_response(dict(alert, notification_type="incident"), {}, "p", "idx")
        incident_id = client.client.index.call_args.kwargs["id"]

        assert alert_id and incident_id
        assert alert_id != incident_id

    def test_alert_documents_are_normalised(self, client):
        """The alert path strips embeddings and Logstash artifacts."""
        alert = {
            "notification_type": "alert",
            "sensor": {"id": "cam-1"},
            "timestamp": "2025-09-19T08:23:06.870Z",
            "embeddings": [{"vector": [0.1]}],
            "@version": "1",
        }
        client.write_event_response(alert, {}, "p", "idx")

        document = client.client.index.call_args.kwargs["document"]
        assert "embeddings" not in document
        assert "@version" not in document

    def test_category_mapping_is_applied_after_fingerprinting(self, client):
        """Renaming a category for display must not change the document id."""
        client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")
        unmapped_id = client.client.index.call_args.kwargs["id"]

        client.write_event_response(
            dict(self.INCIDENT), {}, "p", "idx", category_mapping={"collision": "Crash"}
        )
        kwargs = client.client.index.call_args.kwargs

        assert kwargs["id"] == unmapped_id
        assert kwargs["document"]["category"] == "Crash"

    def test_unmapped_category_is_left_alone(self, client):
        client.write_event_response(
            dict(self.INCIDENT), {}, "p", "idx", category_mapping={"fire": "Fire"}
        )
        assert client.client.index.call_args.kwargs["document"]["category"] == "collision"

    def test_verdict_description_override(self, client):
        client.write_event_response(
            dict(self.INCIDENT), {}, "p", "idx",
            verdict_description_mapping={"collision": {"yes": "Confirmed crash"}},
        )

        document = client.client.index.call_args.kwargs["document"]
        assert document["analyticsModule"]["description"] == "Confirmed crash"

    def test_verdict_override_does_not_change_the_document_id(self, client):
        client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")
        plain_id = client.client.index.call_args.kwargs["id"]

        client.write_event_response(
            dict(self.INCIDENT), {}, "p", "idx",
            verdict_description_mapping={"collision": {"yes": "Confirmed crash"}},
        )

        assert client.client.index.call_args.kwargs["id"] == plain_id

    def test_unknown_verdict_leaves_the_description_alone(self, client):
        client.write_event_response(
            dict(self.INCIDENT), {}, "p", "idx",
            verdict_description_mapping={"collision": {"no": "Not a crash"}},
        )

        document = client.client.index.call_args.kwargs["document"]
        assert "description" not in document.get("analyticsModule", {})

    def test_verdict_lookup_is_case_insensitive(self, client):
        message = dict(self.INCIDENT, info={"verdict": "YES"})
        client.write_event_response(
            message, {}, "p", "idx",
            verdict_description_mapping={"collision": {"yes": "Confirmed crash"}},
        )

        document = client.client.index.call_args.kwargs["document"]
        assert document["analyticsModule"]["description"] == "Confirmed crash"

    def test_non_string_timestamp_is_stringified_for_the_index_name(self, client):
        message = dict(self.INCIDENT, timestamp=1700000000)
        client.write_event_response(message, {}, "p", "idx")

        assert client.client.index.call_args.kwargs["index"].startswith("idx-")

    def test_the_daily_index_is_ensured_before_writing(self, client):
        client.client.indices.exists.return_value = False

        client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")

        assert client.client.indices.create.call_args.kwargs["index"] == "idx-2025-09-19"

    def test_index_failure_propagates(self, client):
        client.client.index.side_effect = RuntimeError("cluster down")

        with pytest.raises(RuntimeError, match="cluster down"):
            client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")

    def test_verbose_debug_logging_does_not_change_the_payload(self, client, monkeypatch):
        """LOG_VERBOSE_ES only adds a redacted log line; the document is unchanged."""
        monkeypatch.setenv("LOG_VERBOSE_ES", "true")
        with patch("clients.elastic.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")

        document = client.client.index.call_args.kwargs["document"]
        assert document["sensorId"] == "cam-1"
        assert document["category"] == "collision"

    def test_compact_debug_logging_reports_the_payload_size(self, client, monkeypatch):
        monkeypatch.setenv("LOG_VERBOSE_ES", "false")
        with patch("clients.elastic.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            client.write_event_response(dict(self.INCIDENT), {}, "p", "idx")

        logged = [call.args for call in mock_logger.debug.call_args_list]
        assert any("size_bytes" in str(args[0]) for args in logged if args)

    def test_unserialisable_document_still_indexes_under_debug(self, client, monkeypatch):
        """The size probe must not abort the write for a non-JSON payload."""
        monkeypatch.setenv("LOG_VERBOSE_ES", "false")
        message = dict(self.INCIDENT, blob=object())

        with patch("clients.elastic.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            client.write_event_response(message, {}, "p", "idx")

        assert client.client.index.call_args.kwargs["document"]["sensorId"] == "cam-1"
