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

"""Unit tests for ``mdx.source.source_kafka``.

``read_data`` is the live ingest entry point — ``enhance_alert_with_vlm.py``
calls it on every loop iteration. What it returns shapes everything
downstream, so the batching contract is pinned here:

* messages are grouped by *kind* (``incident`` / ``alert``), derived from the
  topic name in config, not by topic or partition;
* ``kafka_published_at`` is the **earliest** producer timestamp in the batch
  (that is what latency metrics measure against), while ``kafka_consumed_at``
  is stamped once after every topic has been drained;
* empty kinds are dropped so a quiet topic does not emit an empty batch.

Consumers are created lazily and cached per topic — reconnecting on every
poll would reset the consumer group and replay offsets.

``KafkaMessageBroker`` is patched out; no broker is contacted.

The key-alignment classifier has its own tests in
``test/unit/test_source_kafka_alignment.py`` and is not re-covered here.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mdx.source.source_kafka import MockKafkaMessage, SourceKafka

NEW_CONFIG = {
    "event_bridge": {
        "kafka_source": {
            "group_id": "alert-bridge",
            "topics": {
                "incident": "mdx-incidents",
                "alert": "mdx-alerts",
                "heartbeat": "mdx-heartbeats",
            },
        }
    }
}

LEGACY_CONFIG = {
    "kafka": {
        "anomalyTopic": "legacy-anomalies",
        "group_id": "legacy-group",
        "heartbeat_topic": "legacy-heartbeats",
    }
}


def make_source(config=NEW_CONFIG):
    with patch("mdx.source.source_kafka.KafkaMessageBroker") as broker_cls:
        broker_cls.return_value.get_consumer.side_effect = lambda topic, group, on_revoke=None, on_assignment_change=None: MagicMock(
            name=f"consumer:{topic}"
        )
        return SourceKafka(config)


def payload(sensor_id="cam-1"):
    return json.dumps({"sensorId": sensor_id}).encode("utf-8")


@pytest.fixture
def source():
    return make_source()


class TestConstructionNewLayout:
    def test_topics_are_split_by_kind(self, source):
        assert sorted(source.source_topics) == ["mdx-alerts", "mdx-incidents"]
        assert source.heartbeat_topic == "mdx-heartbeats"
        assert source.topic_to_kind == {"mdx-incidents": "incident", "mdx-alerts": "alert"}

    def test_group_id_is_read_from_config(self, source):
        assert source.groupId == "alert-bridge"

    def test_missing_group_id_raises(self):
        config = {"event_bridge": {"kafka_source": {"topics": {"alert": "mdx-alerts"}}}}
        with pytest.raises(ValueError, match="group_id must be configured"):
            make_source(config)

    def test_heartbeat_only_config_raises(self):
        config = {
            "event_bridge": {
                "kafka_source": {"group_id": "g", "topics": {"heartbeat": "mdx-heartbeats"}}
            }
        }
        with pytest.raises(ValueError, match="At least one non-heartbeat topic"):
            make_source(config)

    def test_blank_topic_values_are_skipped(self):
        config = {
            "event_bridge": {
                "kafka_source": {
                    "group_id": "g",
                    "topics": {"alert": "mdx-alerts", "incident": ""},
                }
            }
        }
        source = make_source(config)
        assert source.source_topics == ["mdx-alerts"]

    def test_no_heartbeat_topic_leaves_it_unset(self):
        config = {
            "event_bridge": {"kafka_source": {"group_id": "g", "topics": {"alert": "mdx-alerts"}}}
        }
        assert make_source(config).heartbeat_topic is None

    def test_consumers_are_not_created_eagerly(self, source):
        assert source.topic_consumer_map == {}


class TestConstructionLegacyLayout:
    def test_falls_back_to_the_top_level_kafka_block(self):
        source = make_source(LEGACY_CONFIG)

        assert source.source_topics == ["legacy-anomalies"]
        assert source.groupId == "legacy-group"
        assert source.heartbeat_topic == "legacy-heartbeats"
        assert source.topic_to_kind == {"legacy-anomalies": "anomaly"}

    def test_heartbeat_topic_has_a_default(self):
        config = {"kafka": {"anomalyTopic": "a", "group_id": "g"}}
        assert make_source(config).heartbeat_topic == "its-streaming-heartbeats"

    def test_empty_topics_block_falls_through_to_legacy(self):
        config = {
            "event_bridge": {"kafka_source": {"topics": {}}},
            "kafka": {"anomalyTopic": "a", "group_id": "g"},
        }
        assert make_source(config).source_topics == ["a"]

    def test_missing_legacy_topic_raises(self):
        with pytest.raises(KeyError):
            make_source({"kafka": {"group_id": "g"}})


class TestEnsureConsumer:
    def test_creates_a_consumer_on_first_use(self, source):
        source._ensure_consumer("mdx-alerts")

        assert "mdx-alerts" in source.topic_consumer_map
        # The hooks are dereferenced when the callback fires rather than
        # captured here, so what matters is that both are wired, not their
        # value at subscribe time.
        topic, group = source.kafka_message_broker.get_consumer.call_args.args
        hooks = source.kafka_message_broker.get_consumer.call_args.kwargs
        assert (topic, group) == ("mdx-alerts", "alert-bridge")
        assert callable(hooks["on_revoke"]) and callable(hooks["on_assignment_change"])

    def test_the_consumer_is_cached(self, source):
        source._ensure_consumer("mdx-alerts")
        first = source.topic_consumer_map["mdx-alerts"]
        source._ensure_consumer("mdx-alerts")

        assert source.topic_consumer_map["mdx-alerts"] is first
        assert source.kafka_message_broker.get_consumer.call_count == 1

    def test_each_topic_gets_its_own_consumer(self, source):
        source._ensure_consumer("mdx-alerts")
        source._ensure_consumer("mdx-incidents")

        assert len(source.topic_consumer_map) == 2


class TestReadData:
    def test_groups_messages_by_kind(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"cam-1", payload(), 1700000000000)]},
            {"mdx-alerts-0": [(b"cam-2", payload("cam-2"), 1700000000500)]},
        ]

        batches = source.read_data()

        assert {b["kind"] for b in batches} == {"incident", "alert"}
        assert len(batches) == 2

    def test_each_partition_becomes_its_own_batch(self, source):
        # Merging partitions into one batch per kind threw away the only thing
        # that says which partition work belongs to, and a rebalance cannot
        # drain what it cannot attribute.
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {
                "mdx-incidents-0": [(b"cam-1", payload(), 1700000000000)],
                "mdx-incidents-1": [(b"cam-2", payload("cam-2"), 1700000000001)],
            },
            {},
        ]

        batches = source.read_data()

        assert len(batches) == 2
        assert all(len(b["messages"]) == 1 for b in batches)
        assert sorted(b["partition"] for b in batches) == [0, 1]
        assert {b["topic"] for b in batches} == {"mdx-incidents"}
        assert {b["kind"] for b in batches} == {"incident"}

    def test_a_hyphenated_topic_name_does_not_confuse_the_partition(self, source):
        # The key is "<topic>-<partition>" and every shipped topic name has
        # hyphens in it, so the number has to come off the right.
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-11": [(b"cam-1", payload(), 1700000000000)]},
            {},
        ]

        batches = source.read_data()

        assert batches[0]["topic"] == "mdx-incidents"
        assert batches[0]["partition"] == 11

    def test_empty_kinds_are_dropped(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [{}, {}]
        assert source.read_data() == []

    def test_empty_partition_lists_are_skipped(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": []},
            {},
        ]
        assert source.read_data() == []

    def test_published_at_is_the_earliest_producer_timestamp(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {
                "mdx-incidents-0": [
                    (b"cam-1", payload(), 1700000005000),
                    (b"cam-2", payload("cam-2"), 1700000001000),
                ]
            },
            {},
        ]

        batches = source.read_data()

        assert batches[0]["kafka_published_at"].startswith("2023-11-14T")
        assert batches[0]["kafka_published_at"].endswith("+00:00")

    def test_published_at_spans_topics(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", payload(), 1700000009000)]},
            {"mdx-alerts-0": [(b"k", payload(), 1700000002000)]},
        ]

        batches = source.read_data()
        assert len({b["kafka_published_at"] for b in batches}) == 1

    @pytest.mark.parametrize("bad_ts", [None, 0, -1])
    def test_missing_or_invalid_timestamps_yield_no_published_at(self, source, bad_ts):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", payload(), bad_ts)]},
            {},
        ]

        assert source.read_data()[0]["kafka_published_at"] is None

    def test_two_element_tuples_are_tolerated(self, source):
        """Older broker paths emit ``(key, value)`` with no timestamp."""
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", payload())]},
            {},
        ]

        batches = source.read_data()
        assert batches[0]["kafka_published_at"] is None
        assert len(batches[0]["messages"]) == 1

    def test_consumed_at_is_shared_by_every_batch(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", payload(), 1700000000000)]},
            {"mdx-alerts-0": [(b"k", payload(), 1700000000000)]},
        ]

        batches = source.read_data()
        assert len({b["kafka_consumed_at"] for b in batches}) == 1

    def test_unmapped_topic_is_labelled_unknown(self, source):
        source.source_topics = ["mystery-topic"]
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mystery-topic-0": [(b"k", payload(), 1700000000000)]}
        ]

        assert source.read_data()[0]["kind"] == "unknown"

    def test_key_alignment_is_recorded_per_message(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"cam-1", payload(), 1700000000000)]},
            {},
        ]

        with patch("mdx.source.source_kafka._record_key_alignment") as record:
            source.read_data()

        record.assert_called_once_with(b"cam-1", payload())

    def test_messages_are_passed_through_verbatim(self, source):
        record = (b"cam-1", payload(), 1700000000000)
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [record]},
            {},
        ]

        assert source.read_data()[0]["messages"] == [record]


class TestRead:
    def test_returns_raw_bytes(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", b"raw-bytes", 1)]},
            {},
        ]
        assert source.read() == [b"raw-bytes"]

    def test_non_bytes_values_are_encoded(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", "text-value", 1)]},
            {},
        ]
        assert source.read() == [b"text-value"]

    def test_messages_from_every_topic_are_returned(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [
            {"mdx-incidents-0": [(b"k", b"a", 1)]},
            {"mdx-alerts-0": [(b"k", b"b", 1)]},
        ]
        assert sorted(source.read()) == [b"a", b"b"]

    def test_broker_failure_degrades_to_an_empty_list(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = RuntimeError("kaboom")
        assert source.read() == []

    def test_empty_poll_returns_an_empty_list(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = [{}, {}]
        assert source.read() == []


class TestPollHeartbeats:
    def test_no_heartbeat_topic_returns_an_empty_list(self):
        config = {
            "event_bridge": {"kafka_source": {"group_id": "g", "topics": {"alert": "mdx-alerts"}}}
        }
        assert make_source(config).poll_heartbeats() == []

    def test_creates_a_consumer_for_the_heartbeat_topic(self, source):
        source.kafka_message_broker.get_consumed_messages.return_value = {}

        source.poll_heartbeats()

        assert "mdx-heartbeats" in source.topic_consumer_map

    def test_decodes_heartbeat_payloads(self, source):
        source.kafka_message_broker.get_consumed_messages.return_value = {
            "mdx-heartbeats-0": [(b"k", b'{"eventId": "hb-1"}', 1)]
        }

        with patch(
            "utils.field_extractor.extract_core_fields", return_value={"message_id": "hb-1"}
        ), patch("utils.field_extractor.validate_required_fields", return_value=True):
            results = source.poll_heartbeats()

        assert len(results) == 1
        assert results[0].id == "hb-1"

    def test_non_bytes_payloads_are_stringified(self, source):
        source.kafka_message_broker.get_consumed_messages.return_value = {
            "mdx-heartbeats-0": [(b"k", '{"eventId": "hb-1"}', 1)]
        }

        with patch("utils.field_extractor.extract_core_fields", return_value={}), patch(
            "utils.field_extractor.validate_required_fields", return_value=True
        ):
            assert len(source.poll_heartbeats()) == 1

    def test_undecodable_heartbeat_is_skipped(self, source):
        source.kafka_message_broker.get_consumed_messages.return_value = {
            "mdx-heartbeats-0": [(b"k", b"{not json", 1), (b"k", b"{}", 1)]
        }

        with patch("utils.field_extractor.extract_core_fields", return_value={}), patch(
            "utils.field_extractor.validate_required_fields", return_value=True
        ):
            assert len(source.poll_heartbeats()) == 1

    def test_broker_failure_degrades_to_an_empty_list(self, source):
        source.kafka_message_broker.get_consumed_messages.side_effect = RuntimeError("kaboom")
        assert source.poll_heartbeats() == []


class TestMockKafkaMessage:
    def test_exposes_the_confluent_accessor_shape(self):
        message = MockKafkaMessage(b"k", b"v", 3, 99)

        assert message.key() == b"k"
        assert message.value() == b"v"
        assert message.partition() == 3
        assert message.offset() == 99


class TestClose:
    def test_closes_every_cached_consumer(self, source):
        source._ensure_consumer("mdx-alerts")
        source._ensure_consumer("mdx-incidents")
        consumers = list(source.topic_consumer_map.values())

        source.close()

        for consumer in consumers:
            consumer.close.assert_called_once()

    def test_closing_before_any_poll_is_a_noop(self, source):
        source.close()


class TestRecordsStrandedByARevokeAreCounted:
    """A revoke delivered by the poll that filled the batch strands records.

    They are already committed, so they are processed here while the incoming
    owner starts on the same sensors. The drain cannot have counted them, so
    the size of that residual is counted instead of assumed.
    """

    @staticmethod
    def _source(revoked, owned=()):
        from unittest.mock import MagicMock
        from mdx.source.source_kafka import SourceKafka

        source = SourceKafka.__new__(SourceKafka)
        source.source_topics = ["t"]
        source.topic_to_kind = {"t": "incident"}
        source.topic_consumer_map = {"t": MagicMock()}
        source._ensure_consumer = lambda topic: None
        broker = MagicMock()
        broker.get_consumed_messages.return_value = {
            "t-3": [(b"k", b'{"id": "a"}', None), (b"k", b'{"id": "b"}', None)]
        }
        broker.was_revoked.side_effect = (
            lambda consumer, topic, partition: (topic, partition) in revoked
        )
        source.kafka_message_broker = broker
        return source

    def _counted(self, revoked):
        from unittest.mock import patch
        source = self._source(revoked)
        with patch("metrics.recorder.inc_records_read_after_revoke") as counter:
            source.read_data()
        return sum(call.args[0] for call in counter.call_args_list)

    def test_stranded_records_are_counted_once_each(self):
        assert self._counted({("t", 3)}) == 2

    def test_a_missing_metrics_package_does_not_end_the_consume_loop(self):
        # This module guards its metrics import on purpose. An unguarded one
        # here raised ImportError out of read_data, which is caught only by
        # the broad handler that exits the consume loop for good -- turning a
        # packaging problem into a total outage.
        from unittest.mock import patch
        source = self._source({("t", 3)})
        with patch("mdx.source.source_kafka._metrics", None):
            batches = source.read_data()
        assert len(batches) == 1

    def test_records_on_a_partition_still_held_are_not_counted(self):
        # The eager protocol revokes everything and hands most of it straight
        # back; counting that as a stranding pages an operator on every deploy.
        assert self._counted(set()) == 0


class TestTheEagerAssignorIsPinned:
    """revoked() clears the whole owned set; assigned() assigns the whole one.

    Both are written for the eager protocol. Under a cooperative assignor the
    callbacks are incremental, so the owned set would collapse to the
    increment and readiness would drop on a partial revoke. Left overridable,
    a deployment could select that quietly.
    """

    @staticmethod
    def _resolve(value):
        from mdx.kafka_message_broker import _require_eager_assignor
        return _require_eager_assignor(value)

    def test_the_default_is_eager(self):
        assert self._resolve(None) == "range,roundrobin"

    def test_an_explicit_eager_choice_is_honoured(self):
        assert self._resolve("roundrobin") == "roundrobin"

    def test_a_cooperative_assignor_is_refused(self):
        import pytest
        with pytest.raises(ValueError, match="cooperative-sticky"):
            self._resolve("cooperative-sticky")

    def test_a_mixed_request_is_refused_by_its_unsupported_half(self):
        import pytest
        with pytest.raises(ValueError, match="cooperative-sticky"):
            self._resolve("range,cooperative-sticky")
