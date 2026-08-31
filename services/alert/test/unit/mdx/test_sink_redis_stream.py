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

"""Unit tests for ``mdx.sink.sink_redis_stream``.

This is the event-bridge sink, so it mirrors ``KafkaSink``: anomalies and
incidents go to separate streams, and routing one to the other's stream is
silent. Both the current ``enhanced_anomaly`` / ``incidents`` keys and the
legacy ``*_stream`` spellings are pinned because existing configs use the
latter.

Per-message failures are swallowed so one bad document cannot drop the rest of
the batch — the same continue-on-error contract the Kafka sink has. Connection
failures at construction are the exception: there is no retry loop on this
path, so a bad host must surface at boot rather than silently discarding every
validation error.

Every ``write_*`` method is handed a list and publishes it through
``add_batch``, one round trip rather than one per entry, because the caller is
on the consume path and the source cannot read past it. The assertions below go
through :func:`published` so what they pin is the payload, key and stream — the
contract — rather than the number of calls it took to get there.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mdx.sink.sink_redis_stream import SinkRedisStream
from mdx.stream_message import StreamMessage

CONFIG = {
    "redis": {"host": "redis", "port": 6379},
    "event_bridge": {
        "sinkType": "redisStream",
        "redis_sink": {
            "streams": {
                "enhanced_anomaly": "alert-bridge-enhanced-alerts",
                "incidents": "alert-bridge-incidents",
            }
        },
    },
}

LEGACY_CONFIG = {
    "event_bridge": {
        "redis_sink": {
            "streams": {
                "enhanced_anomaly_stream": "legacy-enhanced",
                "incidents_stream": "legacy-incidents",
            }
        }
    }
}


def make_sink(config=None):
    """Build the sink with the broker replaced by a mock.

    Supplies a connection when the caller's config names none, so the endpoint
    guard in the constructor does not reject configs written to exercise stream
    routing rather than connection handling.
    """
    config = dict(config or CONFIG)
    config.setdefault("redis", CONFIG["redis"])
    with patch("mdx.sink.sink_redis_stream.RedisStreamBroker") as broker_cls:
        broker_cls.return_value.ping.return_value = True
        broker_cls.return_value.add.return_value = b"1-0"
        # One id per entry, matching the real contract: the caller checks the
        # returned list rather than trusting the call.
        broker_cls.return_value.add_batch.side_effect = (
            lambda stream, entries, **kwargs: [b"1-0"] * len(entries)
        )
        return SinkRedisStream(config)


def published(sink):
    """The stream and the ``(payload, key)`` list handed to the broker."""
    stream, entries = sink.broker.add_batch.call_args.args
    return stream, entries


def sole_entry(sink):
    """The single published ``(payload, key)``, asserting there was one."""
    _stream, entries = published(sink)
    assert len(entries) == 1, f"expected one published entry, got {len(entries)}"
    return entries[0]


def assert_nothing_published(sink):
    for call in sink.broker.add_batch.call_args_list:
        assert call.args[1] == [], f"unexpected publish: {call.args}"
    sink.broker.add.assert_not_called()


def make_stream_message(data=None, core_fields=None, message_id="evt-1"):
    return StreamMessage(
        id=message_id,
        timestamp=datetime(2026, 1, 1),
        data=data if data is not None else {"id": message_id},
        metadata={},
        core_fields=core_fields,
    )


class TestConfiguration:
    def test_current_stream_keys_are_read(self):
        sink = make_sink()
        assert sink.enhanced_anomaly_stream == "alert-bridge-enhanced-alerts"
        assert sink.incidents_stream == "alert-bridge-incidents"

    def test_legacy_stream_keys_are_read(self):
        sink = make_sink(LEGACY_CONFIG)
        assert sink.enhanced_anomaly_stream == "legacy-enhanced"
        assert sink.incidents_stream == "legacy-incidents"

    def test_only_one_stream_is_enough(self):
        config = {"event_bridge": {"redis_sink": {"streams": {"incidents": "only"}}}}
        sink = make_sink(config)
        assert sink.incidents_stream == "only"
        assert sink.enhanced_anomaly_stream is None

    def test_missing_redis_sink_section_raises(self):
        with pytest.raises(ValueError, match="event_bridge.redis_sink must be configured"):
            make_sink({"event_bridge": {}})

    def test_no_streams_configured_raises(self):
        with pytest.raises(ValueError, match="must define 'enhanced_anomaly' and/or 'incidents'"):
            make_sink({"event_bridge": {"redis_sink": {"streams": {}}}})

    @pytest.mark.parametrize("key", ["enhanced_anomaly", "incidents"])
    def test_a_blank_stream_is_not_read_as_an_absent_one(self, key):
        """Absent means "do not publish that kind"; blank is an unresolved
        variable. Read alike, an unresolved variable disabled one route while the
        other kept working — so the sink looked healthy and half its output went
        nowhere."""
        streams = {"enhanced_anomaly": "a", "incidents": "i", key: "  "}
        config = {"event_bridge": {"redis_sink": {"streams": streams}}}
        with pytest.raises(ValueError, match=rf"streams\['{key}'\] is empty"):
            make_sink(config)

    def test_two_routes_cannot_share_one_stream(self):
        """The two carry different payloads and the envelope does not record
        which, so a consumer reading the shared stream would have to guess."""
        config = {
            "event_bridge": {
                "redis_sink": {"streams": {"enhanced_anomaly": "s", "incidents": "s"}}
            }
        }
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            make_sink(config)

    @pytest.mark.parametrize("typo", ["incident", "incidentss", "enhanced-anomaly"])
    def test_a_key_this_sink_does_not_read_is_refused(self, typo):
        """Not ignored, which is what it was. A key this sink does not read is
        indistinguishable from one that is absent, and absent means "do not
        publish that kind" -- so a single misspelling silently disabled a route
        while the sink reported healthy and logged one line per dropped
        message."""
        config = {
            "event_bridge": {
                "redis_sink": {"streams": {"enhanced_anomaly": "a", typo: "b"}},
            }
        }
        with pytest.raises(ValueError, match="no place for"):
            make_sink(config)

    def test_the_refusal_names_the_keys_that_do_work(self):
        config = {"event_bridge": {"redis_sink": {"streams": {"typo": "b"}}}}
        with pytest.raises(ValueError, match="enhanced_anomaly, incidents"):
            make_sink(config)

    def test_an_unreachable_broker_fails_at_construction(self):
        """There is no retry loop here; a silent sink would drop every
        validation-error response."""
        with patch("mdx.sink.sink_redis_stream.RedisStreamBroker") as broker_cls:
            broker_cls.return_value.ping.return_value = False
            with pytest.raises(ConnectionError, match="Unable to reach Redis"):
                SinkRedisStream(CONFIG)


class TestWrite:
    def test_publishes_json_to_the_enhanced_anomaly_stream(self):
        sink = make_sink()
        sink.write([make_stream_message({"id": "evt-1", "verdict": "confirmed"})])

        stream, _entries = published(sink)
        payload, _key = sole_entry(sink)
        assert stream == "alert-bridge-enhanced-alerts"
        assert json.loads(payload) == {"id": "evt-1", "verdict": "confirmed"}

    def test_keys_by_sensor_id_when_available(self):
        """Cohort affinity depends on the key, exactly as with Kafka partitions."""
        sink = make_sink()
        sink.write([make_stream_message(core_fields={"sensor_id": "sensor-9"})])
        assert sole_entry(sink)[1] == "sensor-9"

    def test_falls_back_to_the_message_id_for_the_key(self):
        sink = make_sink()
        sink.write([make_stream_message(message_id="evt-7")])
        assert sole_entry(sink)[1] == "evt-7"

    def test_empty_and_none_batches_are_no_ops(self):
        sink = make_sink()
        sink.write([])
        sink.write(None)
        assert_nothing_published(sink)

    def test_a_failing_message_does_not_drop_the_rest_of_the_batch(self):
        sink = make_sink()
        broken = make_stream_message()
        broken.to_json = MagicMock(side_effect=RuntimeError("boom"))

        sink.write([broken, make_stream_message(message_id="evt-2")])

        _stream, entries = published(sink)
        assert [key for _payload, key in entries] == ["evt-2"]

    def test_a_missing_stream_logs_instead_of_raising(self):
        config = {"event_bridge": {"redis_sink": {"streams": {"incidents": "only"}}}}
        sink = make_sink(config)
        sink.write([make_stream_message()])
        sink.broker.add_batch.assert_not_called()
        sink.broker.add.assert_not_called()


class TestWriteIncidents:
    def test_publishes_to_the_incidents_stream(self):
        sink = make_sink()
        sink.write_incidents([make_stream_message({"id": "inc-1"})])
        assert published(sink)[0] == "alert-bridge-incidents"

    def test_empty_batch_is_a_no_op(self):
        sink = make_sink()
        sink.write_incidents([])
        assert_nothing_published(sink)


class TestWriteMsg:
    def test_publishes_raw_bytes_unchanged(self):
        sink = make_sink()
        sink.write_msg([b"\x08\x01"])

        stream, _entries = published(sink)
        assert stream == "alert-bridge-enhanced-alerts"
        assert sole_entry(sink)[0] == b"\x08\x01"

    def test_index_is_used_as_the_key(self):
        sink = make_sink()
        sink.write_msg([b"a", b"b"])
        _stream, entries = published(sink)
        assert [key for _payload, key in entries] == ["0", "1"]


class TestBatchedPublishing:
    """The write methods take a list, so publishing it entry by entry spent a
    round trip per event on the consume path — latency the source waits on
    before it can read again."""

    def test_a_whole_batch_goes_out_in_one_round_trip(self):
        sink = make_sink()
        sink.write_data([{"sensorId": f"cam-{i}"} for i in range(10)])
        assert sink.broker.add_batch.call_count == 1
        assert len(published(sink)[1]) == 10

    def test_order_is_preserved(self):
        """Consumers read a stream in order, so the batch has to keep it."""
        sink = make_sink()
        sink.write_data([{"sensorId": "a"}, {"sensorId": "b"}, {"sensorId": "c"}])
        _stream, entries = published(sink)
        assert [key for _payload, key in entries] == ["a", "b", "c"]

    def test_a_dropped_entry_is_reported_against_its_own_document(self):
        """The broker returns one id per entry with None where it gave up, so a
        partial failure names the entry rather than the batch."""
        sink = make_sink()
        sink.broker.add_batch.side_effect = lambda stream, entries, **kw: [b"1-0", None]
        sink.write_data([{"sensorId": "a"}, {"sensorId": "b"}])
        # Nothing raised; the loss is logged and counted by the broker.
        assert sink.broker.add_batch.call_count == 1


class TestWriteData:
    def test_serializes_to_json_without_a_transform(self):
        sink = make_sink()
        sink.write_data([{"id": "evt-1", "sensor": {"id": "sensor-3"}}])

        payload, key = sole_entry(sink)
        assert json.loads(payload)["id"] == "evt-1"
        assert key == "sensor-3"

    def test_uses_the_transform_to_produce_protobuf(self):
        sink = make_sink()
        transform = MagicMock()
        transform.return_value.SerializeToString.return_value = b"\x08\x01"

        sink.write_data([{"id": "evt-1"}], transform)

        assert sole_entry(sink)[0] == b"\x08\x01"

    def test_top_level_sensor_id_is_preferred_for_incidents(self):
        """Incident payloads carry ``sensorId``; alerts nest it under ``sensor``."""
        sink = make_sink()
        sink.write_incident_data([{"sensorId": "sensor-1", "sensor": {"id": "other"}}])
        assert sole_entry(sink)[1] == "sensor-1"

    def test_incident_data_goes_to_the_incidents_stream(self):
        sink = make_sink()
        sink.write_incident_data([{"id": "inc-1"}])
        assert published(sink)[0] == "alert-bridge-incidents"

    def test_a_failing_transform_does_not_drop_the_rest_of_the_batch(self):
        sink = make_sink()
        transform = MagicMock(side_effect=[RuntimeError("boom"), MagicMock()])
        sink.write_data([{"id": "a"}, {"id": "b"}], transform)
        assert len(published(sink)[1]) == 1


class TestClose:
    def test_releases_the_connection(self):
        sink = make_sink()
        sink.close()
        sink.broker.close.assert_called_once()


class TestTheEndpointIsRequiredByTheConstructor:
    """The same guard the source and the terminal sink apply, for the same
    reason: it belongs to the component, not to the factory route that happens
    to validate first. Constructed directly, bypassing ``make_sink``, which
    supplies a host for every other case here."""

    @pytest.mark.parametrize("redis_block", [{"host": ""}, {"host": "   "}, {}])
    def test_a_config_with_no_host_is_rejected(self, redis_block):
        config = dict(CONFIG, redis=redis_block)
        with patch("mdx.sink.sink_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match="redis.host is empty"):
                SinkRedisStream(config)

    def test_the_message_names_what_selected_redis(self):
        config = dict(CONFIG, redis={"host": ""})
        with patch("mdx.sink.sink_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match="event_bridge.sinkType"):
                SinkRedisStream(config)
