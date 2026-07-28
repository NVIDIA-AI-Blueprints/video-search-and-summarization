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

"""Unit tests for ``mdx.sink.sink_kafka``.

``KafkaSink`` supports two config layouts — the current
``event_bridge.kafka_sink.topics`` block and the legacy top-level ``kafka``
block — and routing enriched anomalies to the wrong topic is silent, so both
layouts are pinned.

Every write method deliberately swallows per-message failures and continues:
one unserialisable document must not take down the batch, and ``flush()`` must
still run so the already-produced messages leave the buffer. That
continue-then-flush contract is what these tests protect.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mdx.protobuf import Behavior as nvSchemaBehavior
from mdx.sink.sink_kafka import KafkaSink
from mdx.stream_message import StreamMessage

NEW_CONFIG = {
    "event_bridge": {
        "kafka_sink": {
            "topics": {"enhanced_anomaly": "mdx-enhanced", "incidents": "mdx-incidents"}
        }
    }
}

LEGACY_CONFIG = {
    "kafka": {"enhanced_anomaly_topic": "legacy-enhanced", "incidents_topic": "legacy-incidents"}
}


def make_sink(config=NEW_CONFIG):
    with patch("mdx.sink.sink_kafka.KafkaMessageBroker") as broker_cls:
        broker_cls.return_value.get_producer.return_value = MagicMock(name="producer")
        return KafkaSink(config)


def make_stream_message(data=None, core_fields=None, message_id="evt-1"):
    from datetime import datetime

    return StreamMessage(
        id=message_id,
        timestamp=datetime(2021, 1, 1),
        data=data if data is not None else {"eventId": message_id},
        metadata={},
        core_fields=core_fields,
    )


@pytest.fixture
def sink():
    return make_sink()


class TestConstruction:
    def test_reads_topics_from_the_event_bridge_block(self, sink):
        assert sink.enhanced_anomaly_topic == "mdx-enhanced"
        assert sink.incidents_topic == "mdx-incidents"

    def test_falls_back_to_the_legacy_kafka_block(self):
        legacy = make_sink(LEGACY_CONFIG)
        assert legacy.enhanced_anomaly_topic == "legacy-enhanced"
        assert legacy.incidents_topic == "legacy-incidents"

    def test_legacy_block_without_topics_yields_none(self):
        sink = make_sink({"kafka": {}})
        assert sink.enhanced_anomaly_topic is None
        assert sink.incidents_topic is None

    def test_event_bridge_without_kafka_sink_uses_the_legacy_path(self):
        sink = make_sink({"event_bridge": {"sinkType": "kafka"}, "kafka": {"incidents_topic": "t"}})
        assert sink.incidents_topic == "t"

    def test_config_is_kept_on_the_base_class(self, sink):
        assert sink.config is NEW_CONFIG

    def test_producer_comes_from_the_broker(self):
        with patch("mdx.sink.sink_kafka.KafkaMessageBroker") as broker_cls:
            sink = KafkaSink(NEW_CONFIG)
        assert sink.producer is broker_cls.return_value.get_producer.return_value


class TestWriteData:
    def test_publishes_transformed_protobuf(self, sink):
        item = {"id": "beh-1", "sensor": {"id": "cam-1"}}
        sink.write_data([item], lambda d: nvSchemaBehavior(id=d["id"]))

        kwargs = sink.producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-enhanced"
        assert kwargs["key"] == "cam-1"
        decoded = nvSchemaBehavior()
        decoded.ParseFromString(kwargs["value"])
        assert decoded.id == "beh-1"

    def test_missing_sensor_block_yields_an_empty_key(self, sink):
        sink.write_data([{"id": "beh-1"}], lambda d: nvSchemaBehavior(id=d["id"]))
        assert sink.producer.produce.call_args.kwargs["key"] == ""

    def test_transform_failure_skips_only_that_item(self, sink):
        def transform(item):
            if item["id"] == "bad":
                raise ValueError("cannot transform")
            return nvSchemaBehavior(id=item["id"])

        sink.write_data([{"id": "bad"}, {"id": "good"}], transform)

        assert sink.producer.produce.call_count == 1
        sink.producer.flush.assert_called_once()

    def test_flush_runs_even_for_an_empty_batch(self, sink):
        sink.write_data([], lambda d: nvSchemaBehavior())
        sink.producer.flush.assert_called_once()


class TestWrite:
    def test_publishes_stream_message_json(self, sink):
        sink.write([make_stream_message(core_fields={"sensor_id": "cam-1"})])

        kwargs = sink.producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-enhanced"
        assert kwargs["key"] == "cam-1"
        assert json.loads(kwargs["value"].decode("utf-8")) == {"eventId": "evt-1"}

    def test_key_falls_back_to_the_message_id(self, sink):
        sink.write([make_stream_message(message_id="evt-9")])
        assert sink.producer.produce.call_args.kwargs["key"] == "evt-9"

    def test_empty_batch_returns_without_flushing(self, sink):
        sink.write([])
        sink.producer.produce.assert_not_called()
        sink.producer.flush.assert_not_called()

    def test_publish_failure_skips_only_that_message(self, sink):
        sink.producer.produce.side_effect = [RuntimeError("queue full"), None]

        sink.write([make_stream_message(message_id="a"), make_stream_message(message_id="b")])

        assert sink.producer.produce.call_count == 2
        sink.producer.flush.assert_called_once()

    def test_all_messages_are_published(self, sink):
        sink.write([make_stream_message(message_id=str(i)) for i in range(3)])
        assert sink.producer.produce.call_count == 3


class TestWriteMsg:
    def test_publishes_raw_bytes_keyed_by_index(self, sink):
        sink.write_msg([b"one", b"two"])

        first, second = sink.producer.produce.call_args_list
        assert first.kwargs == {"topic": "mdx-enhanced", "value": b"one", "key": "0"}
        assert second.kwargs["key"] == "1"

    def test_empty_batch_returns_without_flushing(self, sink):
        sink.write_msg([])
        sink.producer.flush.assert_not_called()

    def test_publish_failure_skips_only_that_message(self, sink):
        sink.producer.produce.side_effect = [RuntimeError("queue full"), None]

        sink.write_msg([b"one", b"two"])

        assert sink.producer.produce.call_count == 2
        sink.producer.flush.assert_called_once()


class TestWriteIncidents:
    def test_publishes_to_the_incidents_topic(self, sink):
        sink.write_incidents([make_stream_message(core_fields={"sensor_id": "cam-1"})])

        kwargs = sink.producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-incidents"
        assert kwargs["key"] == "cam-1"

    def test_empty_batch_returns_without_flushing(self, sink):
        sink.write_incidents([])
        sink.producer.flush.assert_not_called()

    def test_publish_failure_skips_only_that_incident(self, sink):
        sink.producer.produce.side_effect = [RuntimeError("queue full"), None]

        sink.write_incidents(
            [make_stream_message(message_id="a"), make_stream_message(message_id="b")]
        )

        assert sink.producer.produce.call_count == 2


class TestWriteIncidentData:
    def test_serialises_to_json_without_a_transform(self, sink):
        item = {"id": "inc-1", "sensor": {"id": "cam-1"}}
        sink.write_incident_data([item])

        kwargs = sink.producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-incidents"
        assert kwargs["key"] == "cam-1"
        assert json.loads(kwargs["value"].decode("utf-8")) == item

    def test_transform_result_is_serialised_as_protobuf(self, sink):
        sink.write_incident_data(
            [{"id": "inc-1"}], lambda d: nvSchemaBehavior(id=d["id"])
        )

        decoded = nvSchemaBehavior()
        decoded.ParseFromString(sink.producer.produce.call_args.kwargs["value"])
        assert decoded.id == "inc-1"

    def test_unserialisable_item_skips_only_that_item(self, sink):
        sink.write_incident_data([{"blob": object()}, {"id": "inc-2"}])

        assert sink.producer.produce.call_count == 1
        sink.producer.flush.assert_called_once()

    def test_flush_runs_even_for_an_empty_batch(self, sink):
        sink.write_incident_data([])
        sink.producer.flush.assert_called_once()


class TestClose:
    def test_close_flushes_the_producer(self, sink):
        sink.close()
        sink.producer.flush.assert_called_once()
